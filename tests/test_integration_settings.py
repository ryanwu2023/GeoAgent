import json
import httpx

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.integrations import managed_principal
from backend.store import db

def test_explicit_pichat_ca_preserves_chain_and_hostname_checks(monkeypatch):
    import ssl,certifi
    from backend.integrations import pichat_tls_context
    monkeypatch.delenv('GEOAGENT_PICHAT_CA_FILE', raising=False)
    assert pichat_tls_context() is True
    monkeypatch.setenv('GEOAGENT_PICHAT_CA_FILE', certifi.where())
    context=pichat_tls_context()
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT


def test_local_pichat_override_never_reuses_saved_key(client, monkeypatch):
    from backend.integrations import settings
    payload = dict(origin='http://127.0.0.1:9172', agent_id='geo-research-assistant',
                   phone='13000000000', entry='geo', product_user_id='geo-test', app_key='old-site-secret')
    assert client.put('/api/integrations/pichat', json=payload).status_code == 200
    monkeypatch.setenv('GEOAGENT_LOCAL_PICHAT_ORIGIN', 'http://127.0.0.1:5585')
    assert settings()['origin'] == 'http://127.0.0.1:5585'
    assert settings()['app_key'] == ''
    assert client.put('/api/integrations/pichat', json={**payload, 'origin':'http://127.0.0.1:5585'}).status_code == 409
    assert client.get('/api/pichat/embed-identity').status_code == 503
    monkeypatch.setenv('GEOAGENT_LOCAL_PICHAT_APP_KEY', 'local-only-secret')
    assert settings()['app_key'] == 'local-only-secret'
    monkeypatch.delenv('GEOAGENT_LOCAL_PICHAT_ORIGIN')
    assert settings()['origin'] == payload['origin']
    assert settings()['app_key'] == 'old-site-secret'


def test_local_pichat_override_rejects_public_origin(client, monkeypatch):
    monkeypatch.setenv('GEOAGENT_LOCAL_PICHAT_ORIGIN', 'https://example.com')
    assert client.get('/api/integrations/public').status_code == 422


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GEOAGENT_SETTINGS_TOKEN", "test-management")
    with TestClient(app) as client:
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
    assert public == {"origin": "https://chat.example.com", "agent_id": "geo-test", "product_user_id": "researcher", "embed_configured": True}


@pytest.mark.parametrize("origin", ["file:///tmp", "https://user:password@example.com", "https://example.com/admin", "https://example.com?key=bad", "https://[broken", "https://example.com:bad", "https://exam ple.com"])
def test_bad_origin(client, origin):
    assert client.put("/api/integrations/pichat", json=dict(origin=origin, agent_id="geo", phone="")).status_code == 422


def test_no_separate_management_password_and_cross_site_protection(client, monkeypatch):
    assert client.get("/api/integrations", headers={"X-GeoAgent-Admin": "bad"}).status_code == 200
    assert client.post("/api/integrations/mcp-keys", json={"label": "bad"}, headers={"Origin": "https://evil.example"}).status_code == 403
    monkeypatch.delenv("GEOAGENT_SETTINGS_TOKEN")
    assert client.get("/api/integrations").status_code == 200
    with TestClient(app, client=("127.0.0.1", 9000), base_url="http://localhost") as local:
        assert local.get("/api/integrations").status_code == 200
        assert local.get("/api/integrations", headers={"X-Forwarded-For":"8.8.8.8"}).status_code == 200


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


def test_delete_only_revoked_key(client):
    key = client.post("/api/integrations/mcp-keys", json={"label": "cleanup-test"}).json()
    url = f"/api/integrations/mcp-keys/{key['id']}"
    assert client.delete(url + "/record").status_code == 409
    assert managed_principal(key["key"]) is not None
    assert client.delete(url).status_code == 200
    assert client.delete(url + "/record").status_code == 200
    assert client.delete(url + "/record").status_code == 200
    assert key["id"] not in {item["id"] for item in client.get("/api/integrations/mcp-keys").json()}
    assert managed_principal(key["key"]) is None
    assert client.delete("/api/integrations/mcp-keys/nonexistent/record").status_code == 404


def test_legacy_key_schema_concurrent_migration(client):
    from concurrent.futures import ThreadPoolExecutor
    from backend.integrations import key_table
    with db() as conn:
        conn.execute("CREATE TABLE integration_mcp_keys (id TEXT PRIMARY KEY, label TEXT NOT NULL, token_hash TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT)")
    def migrate(_):
        with db() as conn:
            key_table(conn)
            assert "deleted_at" in {row[1] for row in conn.execute("PRAGMA table_info(integration_mcp_keys)")}
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(migrate, range(16)))


def test_workbench_real_mcp_and_management_auth(client):
    url = "/api/integrations/mcp-workbench"
    assert client.get(url, headers={"X-GeoAgent-Admin": "bad"}).status_code == 200
    catalog = client.get(url)
    assert catalog.headers["cache-control"] == "no-store"
    assert len(catalog.json()["tools"]) == 17
    assert "inputSchema" in catalog.json()["tools"][0]
    assert catalog.json()["instructions"]
    key = client.post("/api/integrations/mcp-keys", json={"label": "trial"}).json()
    body = {"tool": "geo_snapshot_list", "arguments": {}}
    result = client.post(url + "/call", json=body)
    assert result.status_code == 200, result.text
    assert json.loads(result.json()["result"]["content"][0]["text"])["ok"]
    second = client.post(url + "/call", json=body)
    first_payload = json.loads(result.json()["result"]["content"][0]["text"])
    second_payload = json.loads(second.json()["result"]["content"][0]["text"])
    assert first_payload["audit_event_id"] != second_payload["audit_event_id"]
    calls = client.get(url).json()["calls"]
    assert calls[0]["tool"] == body["tool"] and calls[0]["status"] == "ok"
    assert key["key"] not in json.dumps(calls)
    assert client.post(url + "/call", json={**body,"tool":"unknown"}).status_code == 422
    assert client.post(url + "/call", json={**body,"arguments":{"value":"x"*21000}}).status_code == 422
    assert client.post(url + "/call", json={**body,"tool":"geo_snapshot_get"}).json()["result"]["isError"]
    assert client.delete(f"/api/integrations/mcp-keys/{key['id']}").status_code == 200
    assert client.post(url + "/call", json=body).status_code == 200
    assert client.post("/mcp", json={"jsonrpc":"2.0","id":1,"method":"tools/list"}).status_code == 401
    assert client.post(url + "/call", json=body, headers={"Origin":"https://evil.example"}).status_code == 403


def test_remote_no_password_and_public_mcp_bearer(client, monkeypatch):
    monkeypatch.setenv("GEOAGENT_ALLOWED_ORIGINS", "https://geo.example.com")
    with TestClient(app, base_url="https://geo.example.com", client=("203.0.113.10", 9000)) as remote:
        assert remote.get("/api/integrations/public").status_code == 200
        assert remote.get("/api/integrations").status_code == 200
        headers = {"Origin":"https://geo.example.com"}
        assert remote.get("/api/integrations/mcp-workbench").status_code == 200
        assert remote.post("/api/integrations/mcp-workbench/call", json={"tool":"geo_snapshot_list","arguments":{}}, headers=headers).status_code == 200
        assert remote.post("/mcp", json={}).status_code == 401
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


def test_gateway_headers_not_required_but_cross_site_still_rejected(client, monkeypatch):
    monkeypatch.delenv("GEOAGENT_SETTINGS_TOKEN")
    with TestClient(app, base_url="https://geo.example.com", client=("127.0.0.1", 8080)) as proxy:
        headers={"X-Forwarded-For":"203.0.113.10", "X-GeoAgent-Gateway-User":"owner"}
        assert proxy.get("/api/integrations", headers=headers).status_code == 200
        monkeypatch.setenv("GEOAGENT_TRUST_AUTHENTICATED_GATEWAY", "true")
        assert proxy.get("/api/integrations", headers=headers).status_code == 200
        assert proxy.get("/api/integrations", headers={"X-Forwarded-For":"203.0.113.10"}).status_code == 200
    with TestClient(app, base_url="https://geo.example.com", client=("203.0.113.10", 8080)) as remote:
        assert remote.get("/api/integrations", headers=headers).status_code == 200
        assert remote.post("/api/integrations/mcp-keys", json={"label":"blocked"}, headers={"Origin":"https://evil.example"}).status_code == 403
