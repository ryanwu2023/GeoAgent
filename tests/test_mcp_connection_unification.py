"""One external MCP registry serves management, compatibility and news import."""
import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import app as module, external_mcp, mcp_news


class NewsClient:
    seen = []
    calls = []
    tools = [{"name": "service_status"}, {"name": "knowledge_search"}]

    def __init__(self, config):
        self.config = config
        self.seen.append(dict(config))

    async def connect(self):
        return {"serverInfo": {"name": "test-news"}}

    async def list_tools(self):
        return self.tools

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "service_status":
            return {"ok": True, "data": {"overall": "ok"}}
        return {"ok": True, "data": {"hits": []}}


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COLLECTOR_DATA_ROOT", str(tmp_path / "collectors"))
    monkeypatch.setattr(external_mcp, "McpClient", NewsClient)
    monkeypatch.setattr(module, "McpClient", NewsClient)
    NewsClient.seen = []
    NewsClient.calls = []


def add_server(name="news", key="registry-secret", **kwargs):
    return asyncio.run(external_mcp.test_and_save(
        None, name, "http", f"https://{name}.example.org/mcp", key, **kwargs
    ))["server"]


def legacy_file(tmp_path, verify_tls=False):
    value = {
        "name": "legacy", "url": "https://legacy.example.org/mcp",
        "authorization": "Bearer legacy-secret", "verify_tls": verify_tls,
    }
    (tmp_path / "mcp-config.json").write_text(json.dumps(value), encoding="utf-8")
    return value


def test_legacy_connection_migrates_once_and_keeps_tls(tmp_path):
    legacy = legacy_file(tmp_path)
    config = mcp_news.load_config()
    assert config["authorization"] == legacy["authorization"]
    assert config["verify_tls"] is False
    assert external_mcp.public_registry()["servers"][0]["verify_tls"] is False
    external_mcp.delete_server(config["id"])
    assert (tmp_path / "mcp-config.json").exists()  # recoverable backup
    assert mcp_news.public_config()["configured"] is False
    with pytest.raises(ValueError, match="尚未配置"):
        mcp_news.McpClient()
    assert external_mcp.public_registry()["servers"] == []


def test_empty_registry_is_an_authoritative_migration_boundary(tmp_path):
    assert external_mcp.public_registry()["servers"] == []
    legacy_file(tmp_path)
    assert external_mcp.public_registry()["servers"] == []
    assert mcp_news.load_config()["url"] == ""


def test_registry_save_is_immediately_used_by_news_client():
    server = add_server()
    config = mcp_news.load_config(server["id"])
    assert config["authorization"] == "Bearer registry-secret"
    assert mcp_news.McpClient().config == config
    assert mcp_news.public_config()["server_id"] == server["id"]
    public = external_mcp.public_registry()
    assert "registry-secret" not in json.dumps(public)
    assert public["servers"][0]["news_import"]["supported"] is True


def test_compatibility_save_writes_registry_only_and_invalidates_old_test(tmp_path):
    legacy = legacy_file(tmp_path)
    original = (tmp_path / "mcp-config.json").read_text()
    server = external_mcp.public_registry()["servers"][0]
    asyncio.run(external_mcp.test_and_save(
        server["id"], "legacy", "http", legacy["url"], ""
    ))
    result = mcp_news.save_config(
        "renamed", "https://renamed.example.org/mcp", "", False, server["id"]
    )
    assert result["server_id"] == server["id"]
    assert mcp_news.load_config()["authorization"] == legacy["authorization"]
    assert external_mcp.public_registry()["servers"][0]["name"] == "renamed"
    assert external_mcp.public_registry()["servers"][0]["last_test"] is None
    assert external_mcp.public_registry()["servers"][0]["news_import"]["supported"] is None
    assert (tmp_path / "mcp-config.json").read_text() == original


def test_multiple_connections_require_explicit_selection_without_poisoning_status():
    one, two = add_server("one"), add_server("two")
    assert mcp_news.public_config()["selection_required"] is True
    assert mcp_news.public_config()["configured"] is False
    with pytest.raises(ValueError, match="明确选择 server_id"):
        mcp_news.load_config()
    with pytest.raises(ValueError, match="明确选择 server_id"):
        mcp_news.save_config("other", "https://other.example.org/mcp", "new", True)
    assert mcp_news.load_config(one["id"])["name"] == "one"
    assert mcp_news.load_config(two["id"])["name"] == "two"
    with TestClient(module.app) as client:
        response = client.get("/api/mcp/config")
        assert response.status_code == 200
        assert response.json()["selection_required"] is True
        selected = client.get("/api/mcp/config", params={"server_id": two["id"]})
        assert selected.json()["server_id"] == two["id"]
        assert client.get("/api/mcp/config", params={"server_id": "deleted"}).status_code == 422


def test_edit_keeps_tls_policy_unless_explicitly_changed(tmp_path):
    legacy_file(tmp_path, verify_tls=False)
    server = external_mcp.public_registry()["servers"][0]
    edited = asyncio.run(external_mcp.test_and_save(
        server["id"], server["name"], "http", server["url"], ""
    ))
    assert edited["server"]["verify_tls"] is False
    assert NewsClient.seen[-1]["verify_tls"] is False
    edited = asyncio.run(external_mcp.test_and_save(
        server["id"], server["name"], "http", server["url"], "", True
    ))
    assert edited["server"]["verify_tls"] is True


def test_connectivity_is_distinct_from_news_capability(monkeypatch):
    class OtherClient(NewsClient):
        tools = [{"name": "calculate"}]
    monkeypatch.setattr(external_mcp, "McpClient", OtherClient)
    saved = add_server()
    assert saved["last_test"]["ok"] is True
    assert saved["news_import"] == {
        "supported": False, "required_tools": ["service_status", "knowledge_search"],
        "missing_tools": ["service_status", "knowledge_search"],
    }


def test_news_capability_uses_all_tools_not_truncated_first_twenty(monkeypatch):
    class ManyTools(NewsClient):
        tools = [{"name": f"other-{i}"} for i in range(22)] + NewsClient.tools
    monkeypatch.setattr(external_mcp, "McpClient", ManyTools)
    saved = add_server()
    assert saved["news_import"]["supported"] is True
    assert len(saved["last_test"]["tool_names"]) == 20


def fetch_request(server_id=None):
    return module.McpFetchInput(
        server_id=server_id, query="Iran news", topic="usiran", dimension="diplomacy",
        date_from="2026-09-01", date_to="2026-09-30", top_k=7,
    )


def test_fetch_selected_registry_server_and_use_exact_saved_connection(monkeypatch):
    add_server("unused", "unused-secret")
    selected = add_server("selected", "selected-secret")
    captured = {}
    monkeypatch.setattr(module, "store_news", lambda config, *args: (captured.update(config=config) or None, 0))
    monkeypatch.setattr(module, "load_bundle", lambda root: {"records": [], "errors": []})
    monkeypatch.setattr(module, "save_snapshot", lambda bundle: ("new-test-snapshot", True))
    monkeypatch.setattr(module, "set_working_snapshot_id", lambda sid: captured.update(snapshot=sid))
    monkeypatch.setattr(module, "start_translation", lambda: None)
    result = asyncio.run(module.fetch_mcp_news(fetch_request(selected["id"])))
    assert result["server_id"] == selected["id"]
    assert captured["config"]["url"] == selected["url"]
    assert captured["config"]["authorization"] == "Bearer selected-secret"
    assert NewsClient.calls == [
        ("service_status", {}),
        ("knowledge_search", {
            "query": "Iran news", "top_k": 7, "mode": "hybrid",
            "date_from": "2026-09-01", "date_to": "2026-09-30",
        }),
    ]
    assert "selected-secret" not in json.dumps(result)


@pytest.mark.parametrize("selected", [None, "removed"])
def test_fetch_never_guesses_connection_or_resurrects_deleted_server(selected):
    add_server("one")
    add_server("two")
    before = len(NewsClient.seen)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.fetch_mcp_news(fetch_request(selected)))
    assert exc.value.status_code == 422
    assert len(NewsClient.seen) == before
    assert NewsClient.calls == []
    assert "***" not in exc.value.detail  # empty secret must not corrupt message


def test_missing_news_tools_blocks_before_import_or_calls(monkeypatch):
    server = add_server()
    class OtherClient(NewsClient):
        tools = [{"name": "unrelated"}]
    monkeypatch.setattr(module, "McpClient", OtherClient)
    def unexpected(*args):
        pytest.fail("unsupported service must never import news or snapshots")
    monkeypatch.setattr(module, "store_news", unexpected)
    monkeypatch.setattr(module, "save_snapshot", unexpected)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.fetch_mcp_news(fetch_request(server["id"])))
    assert "缺少工具：service_status、knowledge_search" in exc.value.detail
    assert NewsClient.calls == []


def test_news_test_selects_connection_and_reports_capability():
    add_server("one")
    server = add_server("two")
    result = asyncio.run(module.test_mcp(module.McpTestInput(server_id=server["id"])))
    assert result["ok"] is True
    assert result["server"]["server_id"] == server["id"]
    assert result["news_import"]["supported"] is True
    ambiguous = asyncio.run(module.test_mcp())
    assert ambiguous["ok"] is False
    assert "明确选择 server_id" in ambiguous["message"]


def test_runtime_errors_do_not_reflect_connection_secret(monkeypatch):
    server = add_server()
    class LeakyClient(NewsClient):
        async def list_tools(self):
            raise ValueError("upstream rejected Bearer registry-secret / registry-secret")
    monkeypatch.setattr(module, "McpClient", LeakyClient)
    result = asyncio.run(module.test_mcp(module.McpTestInput(server_id=server["id"])))
    assert "registry-secret" not in json.dumps(result)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.fetch_mcp_news(fetch_request(server["id"])))
    assert "registry-secret" not in exc.value.detail


def test_corrupt_registry_never_falls_back_to_legacy_credentials(tmp_path):
    legacy_file(tmp_path)
    (tmp_path / "mcp-servers.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="注册表格式无效"):
        mcp_news.load_config()
    assert (tmp_path / "mcp-servers.json").read_text() == "[]"


def test_new_connection_compatibility_api_and_registry_share_same_record():
    client = TestClient(module.app)
    created = client.post("/api/mcp/config", json={
        "name": "news", "url": "https://news.example.org/mcp",
        "authorization": "Bearer facade-secret", "verify_tls": True,
    })
    assert created.status_code == 200
    selected_id = created.json()["server_id"]
    registry = client.get("/api/integrations/external-mcp").json()
    assert len(registry["servers"]) == 1
    assert registry["servers"][0]["id"] == selected_id
    assert registry["servers"][0]["news_import"]["supported"] is None
    saved = client.post("/api/integrations/external-mcp/test-and-save", json={
        "id": selected_id, "name": "renamed", "type": "http",
        "url": "https://news.example.org/mcp", "key": "",
    })
    assert saved.status_code == 200
    assert saved.json()["server"]["news_import"]["supported"] is True
    assert client.get("/api/mcp/config").json()["name"] == "renamed"
    assert client.post("/api/mcp/test", json={"server_id": selected_id}).json()["ok"] is True
    assert "facade-secret" not in created.text + saved.text + json.dumps(registry)
    assert client.delete(f"/api/integrations/external-mcp/{selected_id}").status_code == 200
    assert client.get("/api/mcp/config").json()["configured"] is False
