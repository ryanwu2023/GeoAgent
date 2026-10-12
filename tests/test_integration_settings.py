import json
import httpx

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.integrations import managed_principal
from backend.store import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GEOAGENT_SETTINGS_TOKEN", "test-management")
    with TestClient(app, headers={"X-GeoAgent-Admin": "test-management"}) as client:
        yield client


def test_settings_save_redaction_and_normalize(client):
    payload = dict(origin="https://chat.example.com/chat", agent_id="geo-test", phone="13000000000",
                   entry="geo", product_user_id="researcher", app_key="pk-test-secret")
    result = client.put("/api/integrations/pichat", json=payload)
    assert result.status_code == 200
    assert result.json()["origin"] == "https://chat.example.com"
    assert result.json()["app_key_configured"] is True
    assert "pk-test-secret" not in result.text
    payload["app_key"] = ""
    assert client.put("/api/integrations/pichat", json=payload).status_code == 200
    with db() as conn:
        saved = json.loads(conn.execute("SELECT value FROM workspace_state WHERE key='integrations.pichat'").fetchone()[0])
    assert saved["app_key"] == "pk-test-secret"
    public = client.get("/api/integrations/public").json()
    assert public == {"origin": "https://chat.example.com", "agent_id": "geo-test", "product_user_id": "researcher"}


@pytest.mark.parametrize("origin", ["file:///tmp", "https://user:password@example.com", "https://example.com/admin", "https://example.com?key=bad", "https://[broken", "https://example.com:bad", "https://exam ple.com"])
def test_bad_origin(client, origin):
    assert client.put("/api/integrations/pichat", json=dict(origin=origin, agent_id="geo", phone="")).status_code == 422


def test_management_protection(client, monkeypatch):
    assert client.get("/api/integrations", headers={"X-GeoAgent-Admin": "bad"}).status_code == 403
    assert client.post("/api/integrations/mcp-keys", json={"label": "bad"}, headers={"Origin": "https://evil.example"}).status_code == 403
    monkeypatch.delenv("GEOAGENT_SETTINGS_TOKEN")
    assert client.get("/api/integrations").status_code == 403
    with TestClient(app, client=("127.0.0.1", 9000), base_url="http://localhost") as local:
        assert local.get("/api/integrations").status_code == 200
        assert local.get("/api/integrations", headers={"X-Forwarded-For":"8.8.8.8"}).status_code == 403


def test_mcp_live_key_lifecycle(client):
    result = client.post("/api/integrations/mcp-keys", json={"label":"integration-test"})
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    key = result.json()
    assert managed_principal(key["key"]).read_only
    assert key["key"] not in client.get("/api/integrations/mcp-keys").text
    with db() as conn:
        assert key["key"] not in str(dict(conn.execute("SELECT * FROM integration_mcp_keys").fetchone()))
    headers = {"Authorization":f"Bearer {key['key']}", "Accept":"application/json, text/event-stream"}
    def call(method, params=None):
        return client.post("/mcp", headers=headers, json={"jsonrpc":"2.0","id":1,"method":method,"params":params or {}})
    assert client.post("/mcp", json={}).status_code == 401
    init = call("initialize", {"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1"}})
    assert init.status_code == 200, init.text
    listed = call("tools/list")
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["result"]["tools"]) >= 11
    snapshots = call("tools/call", {"name":"geo_snapshot_list","arguments":{}})
    assert snapshots.status_code == 200, snapshots.text
    assert json.loads(snapshots.json()["result"]["content"][0]["text"])["ok"]
    assert client.delete(f"/api/integrations/mcp-keys/{key['id']}").status_code == 200
    assert managed_principal(key["key"]) is None
    assert call("tools/list").status_code == 401
    assert client.get("/healthz").json()["service"] == "geoagent-mcp"


def test_remote_management_token_and_public_host(client, monkeypatch):
    monkeypatch.setenv("GEOAGENT_ALLOWED_ORIGINS", "https://geo.example.com")
    with TestClient(app, base_url="https://geo.example.com", client=("203.0.113.10", 9000)) as remote:
        assert remote.get("/api/integrations/public").status_code == 200
        assert remote.get("/api/integrations").status_code == 403
        headers = {"X-GeoAgent-Admin":"test-management", "Origin":"https://geo.example.com"}
        key = remote.post("/api/integrations/mcp-keys", json={"label":"remote"}, headers=headers).json()["key"]
        result = remote.post("/mcp", json={"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}},
                             headers={"Authorization":f"Bearer {key}", "Accept":"application/json, text/event-stream"})
        assert result.status_code == 200, result.text


def test_identity_uses_saved_config_without_restart(client, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok":True,"data":{"token":"short-lived-test-token","agentId":"saved-agent"}})
    original_client = httpx.AsyncClient
    monkeypatch.setattr("backend.app.httpx.AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    payload = dict(origin="https://pichat.example.com/chat", agent_id="saved-agent", app_key="pk-private",phone="13000000000",entry="",product_user_id="saved-user")
    assert client.put("/api/integrations/pichat", json=payload).status_code == 200
    result = client.get("/api/pichat/embed-identity?agent_id=untrusted-query-agent")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert str(calls[0].url) == "https://pichat.example.com/api/v1/embed/identity"
    assert calls[0].headers["Authorization"] == "Bearer pk-private"
    assert json.loads(calls[0].content)["agentId"] == "saved-agent"
    assert json.loads(calls[0].content)["productUserId"] == "saved-user"
    assert "pk-private" not in result.text
