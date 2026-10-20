"""Registry growth must not break manual-run or exclusion API validation."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import app as module
from backend import collector_config


@pytest.fixture
def registry_ids():
    registry = module.load_registry(module.ROOT / "collectors" / "collectors.json")
    ids = [item["id"] for item in registry if item.get("enabled", True)]
    assert len(ids) >= 25
    return ids


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "research"))
    monkeypatch.setattr(collector_config, "CONFIG_PATH", tmp_path / "collector-settings.json")
    monkeypatch.setattr(module, "load_config", lambda: {
        "exclude_ids": [], "allow_partial_import": False,
    })

    def forbidden_submit(**_kwargs):
        pytest.fail("Validation must never start a real collector")

    monkeypatch.setattr(module.collector_jobs, "submit", forbidden_submit)


@pytest.mark.parametrize(
    "model,field",
    [(module.CollectorRunRequest, "ids"), (module.CollectorConfigRequest, "exclude_ids")],
)
def test_request_models_accept_current_registry(model, field, registry_ids):
    request = model(**{field: registry_ids})
    assert getattr(request, field) == registry_ids


@pytest.mark.parametrize(
    "model,field",
    [(module.CollectorRunRequest, "ids"), (module.CollectorConfigRequest, "exclude_ids")],
)
def test_request_models_retain_bounded_lists(model, field):
    with pytest.raises(ValidationError):
        model(**{field: ["collector"] * 101})


def test_all_registered_collectors_reach_mock_queue(registry_ids, monkeypatch):
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(public=lambda: {"run_id": "test-only", "status": "queued"})

    monkeypatch.setattr(module.collector_jobs, "submit", submit)
    response = TestClient(module.app).post("/api/collector/runs", json={"ids": registry_ids})
    assert response.status_code == 202
    assert len(calls) == 1
    assert calls[0]["ids"] == registry_ids


def test_large_exclusion_list_is_saved_only_to_test_directory(registry_ids, tmp_path):
    response = TestClient(module.app).put(
        "/api/collector/config", json={"exclude_ids": registry_ids, "apply": False},
    )
    assert response.status_code == 200
    assert response.json()["config"]["exclude_ids"] == sorted(registry_ids)
    assert response.json()["applied"] is False
    assert collector_config.CONFIG_PATH == tmp_path / "collector-settings.json"
    assert collector_config.CONFIG_PATH.is_file()


def test_unknown_ids_still_fail_registry_validation(registry_ids, tmp_path):
    ids = [*registry_ids, "not-a-registered-collector"]
    client = TestClient(module.app)
    run = client.post("/api/collector/runs", json={"ids": ids})
    assert run.status_code == 422
    assert "采集器不存在或已停用" in run.json()["detail"]
    config = client.put("/api/collector/config", json={"exclude_ids": ids})
    assert config.status_code == 422
    assert "未知采集器" in config.json()["detail"]
    assert not (tmp_path / "collector-settings.json").exists()


def test_disabled_collectors_cannot_be_queued(registry_ids, monkeypatch):
    disabled_id = registry_ids[0]
    monkeypatch.setattr(module, "load_registry", lambda _path: [
        {"id": collector_id, "enabled": collector_id != disabled_id}
        for collector_id in registry_ids
    ])
    response = TestClient(module.app).post("/api/collector/runs", json={"ids": registry_ids})
    assert response.status_code == 422
    assert disabled_id in response.json()["detail"]
    assert "采集器不存在或已停用" in response.json()["detail"]
