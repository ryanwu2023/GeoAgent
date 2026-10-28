"""API-to-real-translator integration tests; isolated data and simulated HTTP."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import llm, model_settings
from backend.app import app
from backend.chinese import translation_cache
from backend.model_routes import TEST_ID, TEST_TEXT


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(model_settings, "ROOT", tmp_path)
    monkeypatch.setattr(llm, "ROOT", tmp_path)
    for key in model_settings.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return TestClient(app)


def draft(**changes):
    return {
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash", "timeout_seconds": 90,
        "api_key": "test-key", **changes,
    }


def mock_model(monkeypatch, *, content=None, status=200):
    actual = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        incoming = json.loads(body["messages"][-1]["content"])
        assert incoming == {"items": [{"id": TEST_ID, "text": TEST_TEXT}]}
        text = content if content is not None else json.dumps({
            "items": [{"id": TEST_ID, "text": "报告列出10月20日的12艘船。"}],
        })
        return httpx.Response(status, json={
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "error": "test-key must never be echoed",
        })

    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: actual(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    return requests


def test_draft_test_runs_production_translation_without_saving(client, monkeypatch):
    requests = mock_model(monkeypatch)
    before = translation_cache()
    response = client.post("/api/integrations/model/test", json=draft())
    body = response.json()
    assert response.status_code == 200 and body["ok"]
    assert body["source"] == "draft"
    assert body["translation"]["text"] == "报告列出10月20日的12艘船。"
    assert body["translation"]["issues"] == []
    assert len(requests) == 1
    assert response.headers["cache-control"] == "no-store"
    assert "test-key" not in response.text
    assert not model_settings.settings_path().exists()
    assert translation_cache() == before


def test_save_reload_keep_secret_and_effective_test_share_same_configuration(client, monkeypatch):
    requests = mock_model(monkeypatch)
    before = translation_cache()
    saved = client.put("/api/integrations/model", json=draft())
    assert saved.status_code == 200
    assert saved.json()["configured"] and saved.json()["source"] == "saved"
    assert not requests  # Saving is not an API call or translation job.
    public = client.get("/api/integrations/model")
    assert public.json() == saved.json()
    assert set(public.json()) == set(model_settings.PUBLIC_KEYS)
    assert "test-key" not in saved.text + public.text
    assert client.put("/api/integrations/model", json=draft(api_key="")).json()["configured"]
    tested = client.post("/api/model/check").json()
    assert tested["ok"] and tested["translation"]["ok"]
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    assert translation_cache() == before


@pytest.mark.parametrize("changes", [
    {"provider": "unknown"}, {"timeout_seconds": 4}, {"timeout_seconds": 301},
    {"api_key": {"nested": "test-key"}}, {"base_url": "x" * 1001},
])
def test_validation_errors_do_not_echo_submitted_values(client, changes):
    response = client.put("/api/integrations/model", json=draft(**changes))
    assert response.status_code == 422
    assert "test-key" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert not model_settings.settings_path().exists()


def test_switch_endpoint_requires_its_own_key(client):
    assert client.put("/api/integrations/model", json=draft()).status_code == 200
    response = client.post("/api/integrations/model/test", json=draft(
        base_url="https://another.example/v1", api_key="",
    ))
    assert response.status_code == 422
    assert "test-key" not in response.text
    assert client.get("/api/integrations/model").json()["base_url"] == "https://api.deepseek.com"


@pytest.mark.parametrize("content", [
    "连接成功",
    '{"items":[{"id":"model-config-check","text":"报告列出10月20日的13艘船。"}]}',
    '{"items":[{"id":"model-config-check","text":"The report lists 12 ships on 20 October."}]}',
])
def test_test_rejects_nontranslation_or_changed_numbers(client, monkeypatch, content):
    mock_model(monkeypatch, content=content)
    response = client.post("/api/integrations/model/test", json=draft())
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["translation"]["ok"] is False
    assert not model_settings.settings_path().exists()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_provider_errors_are_sanitized(client, monkeypatch, status):
    mock_model(monkeypatch, status=status)
    response = client.post("/api/integrations/model/test", json=draft())
    assert response.status_code == 200 and not response.json()["ok"]
    assert "test-key" not in response.text
    assert "must never be echoed" not in response.text


def test_cross_origin_config_writes_and_tests_rejected(client):
    for path, method in [("/api/integrations/model", client.put), ("/api/integrations/model/test", client.post)]:
        response = method(path, json=draft(), headers={"Origin": "https://other.example"})
        assert response.status_code == 403
    assert not model_settings.settings_path().exists()


@pytest.mark.parametrize("repair", ["disabled", "complete"])
def test_bad_optional_model_config_does_not_stop_app_and_can_be_repaired(client, monkeypatch, repair):
    from backend import chinese
    from backend.adapters import normalize
    from backend.store import save_snapshot, set_working_snapshot_id

    record = normalize({
        "title": "Report lists 12 ships.", "summary": "No translation exists yet.",
        "link": "https://example.test/report",
    }, "test")
    sid, _ = save_snapshot({
        "records": [record], "observations": [], "sources": [], "manifest": [], "errors": [],
    })
    set_working_snapshot_id(sid)
    path = model_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken-sensitive-model-configuration")
    monkeypatch.setenv("LLM_API_KEY", "test-no-fallback")
    monkeypatch.setattr(chinese, "_worker", None)
    monkeypatch.setattr(chinese.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("bad config must not start translation"))
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: pytest.fail("bad config must not call model"))

    with client:
        assert client.get("/healthz").status_code == 200
        status = client.get("/api/status")
        assert status.status_code == 200
        assert status.json()["model"]["configured"] is False
        assert status.json()["model"]["config_error"]
        assert status.json()["translation"]["model_configured"] is False
        public = client.get("/api/integrations/model")
        assert public.status_code == 200
        assert public.json()["source"] == "error"
        assert public.json()["config_error"]
        assert "sensitive" not in public.text and "test-no-fallback" not in public.text
        # A blank draft cannot inherit anything from damaged settings.
        failed = client.post("/api/integrations/model/test", json=draft(api_key=""))
        assert failed.status_code == 422
        payload = {"provider": "disabled"} if repair == "disabled" else draft()
        repaired = client.put("/api/integrations/model", json=payload)
        assert repaired.status_code == 200
        assert repaired.json()["configured"] is (repair == "complete")
        assert "config_error" not in repaired.json()
        assert client.get("/api/integrations/model").json() == repaired.json()


@pytest.mark.parametrize("bad_body", [False, True])
def test_evidence_translator_shares_saved_model_and_validates_before_persisting(client, monkeypatch, bad_body):
    import importlib
    app_module = importlib.import_module("backend.app")
    record = {"id": "evidence-test", "title": "Report lists 12 ships.", "text": "The fleet has 20 ships."}
    monkeypatch.setattr(app_module, "snapshot_or_404", lambda _: {"records": [record]})
    stored = []
    monkeypatch.setattr("backend.chinese.save_translation", lambda *args: stored.append(args))
    client.put("/api/integrations/model", json=draft())
    actual = httpx.AsyncClient

    def handle(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        items = json.loads(body["messages"][-1]["content"])["items"]
        assert items == [{"id": field, "text": record[field]} for field in ("title", "text")]
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps({"items": [
                {"id": "title", "text": "报告列出12艘船。"},
                {"id": "text", "text": "舰队拥有21艘船。" if bad_body else "舰队拥有20艘船。"},
            ]})},
        }]})

    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: actual(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    result = client.post("/api/evidence/evidence-test/translate?snapshot_id=test").json()
    assert result["ok"] is not bad_body
    if bad_body:
        assert stored == []  # Even the valid title must not replace existing data.
    else:
        assert [entry[:2] for entry in stored] == [
            (record["title"], "报告列出12艘船。"), (record["text"], "舰队拥有20艘船。"),
        ]
