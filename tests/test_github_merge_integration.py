"""Regression coverage for GitHub features coexisting with project MCP."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend import app as module
from backend.geo_mcp.auth import AuthConfig


def test_news_config_does_not_shadow_project_mcp_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(module, "start_translation", lambda: None)
    assert isinstance(module.mcp_config, AuthConfig)
    with TestClient(module.app) as client:
        result = client.post("/api/mcp/config", json={
            "name": "news", "url": "https://example.org/mcp",
            "authorization": "Bearer merge-test-only", "verify_tls": True,
        })
        assert result.status_code == 200
        assert result.json()["configured"]
        assert "merge-test-only" not in result.text
        assert client.get("/api/mcp/config").json() == result.json()
        # The project MCP is still initialized, not replaced by the news route.
        assert module.app.state.geo_mcp is not None
        assert client.get("/api/workspace-selection").status_code == 200


def test_status_preserves_working_snapshot_and_adds_legacy_crawler(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(module, "get_working_snapshot_id", lambda: "frozen")
    monkeypatch.setattr(module, "snapshots", lambda: [{"id": "newer"}])
    run_file = tmp_path / "crawler-runs" / "current.json"
    run_file.parent.mkdir()
    run_file.write_text(json.dumps({"state": "running", "total": 2}), encoding="utf-8")
    result = module.status()
    assert result["working_snapshot_id"] == "frozen"
    assert result["crawler"]["state"] == "running"
    assert result["crawler"]["total"] == 2
    assert "authorization" not in result["mcp"]


def test_first_direct_collector_import_syncs_before_selecting_root(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.delenv("CRAWLER_ROOT", raising=False)
    monkeypatch.setenv("COLLECTOR_DATA_ROOT", str(runtime))
    monkeypatch.setattr(module, "start_translation", lambda: None)

    def sync(*, data_root):
        assert data_root == runtime
        output = runtime / "local" / "output"
        output.mkdir(parents=True)
        (output / "geo-v6.json").write_text("{}", encoding="utf-8")
        return {"projects": 1, "files": 1, "bytes": 2}

    def load(root):
        assert root == runtime
        return {"records": [{"id": "local"}], "errors": []}

    monkeypatch.setattr(module, "sync_local_collector_outputs", sync)
    monkeypatch.setattr(module, "load_bundle", load)
    monkeypatch.setattr(module, "save_snapshot", lambda bundle: ("new", True))
    selected = []
    monkeypatch.setattr(module, "set_working_snapshot_id", lambda snapshot_id: selected.append(snapshot_id))
    result = module.import_data()
    assert result["id"] == "new"
    assert result["sync"]["files"] == 1
    assert selected == ["new"]


def test_windows_merge_keeps_process_identity_and_custom_import_port():
    root = Path(__file__).resolve().parents[1]
    start = (root / "start.ps1").read_text(encoding="utf-8")
    stop = (root / "stop.ps1").read_text(encoding="utf-8")
    assert "-eq ([datetime]$saved.createdAt).ToUniversalTime()" in start
    assert "-ne ([datetime]$saved.createdAt).ToUniversalTime()" in stop
    assert "-ne ([datetime]$crawlerSaved.createdAt).ToUniversalTime()" in stop
    assert "$saved.url -eq $url" in start
    assert "Invoke-RestMethod -Uri ($url+'api/status')" in start
    assert "--import-url', ($url+'api/import')" in start
    assert "Start-CrawlerManager $existingPython" in start
    assert "--interval-sec', '86400'" in start
