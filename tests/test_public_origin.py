from fastapi.testclient import TestClient

from backend.app import app


def test_configured_public_origin_is_allowed_without_trusting_host(monkeypatch):
    monkeypatch.setenv("GEOAGENT_ALLOWED_ORIGINS", " https://geoagentd40d.sealosgzg.site ")
    client = TestClient(app)
    path = "/api/workspace-selection"
    # Invalid body deliberately reaches validation without changing the database.
    assert client.put(path, json={}, headers={"Origin": "https://geoagentd40d.sealosgzg.site"}).status_code == 422
    assert client.put(path, json={}, headers={"Origin": "https://evil.test", "Host": "evil.test"}).status_code == 403
    assert client.put(path, json={}, headers={"Origin": "https://geoagentd40d.sealosgzg.site.evil.test"}).status_code == 403
    assert client.put(path, json={}, headers={"Origin": "http://127.0.0.1:8000"}).status_code == 422


def test_public_origin_is_not_allowed_by_default(monkeypatch):
    monkeypatch.delenv("GEOAGENT_ALLOWED_ORIGINS", raising=False)
    client = TestClient(app)
    assert client.put("/api/workspace-selection", json={}, headers={"Origin": "https://geoagentd40d.sealosgzg.site"}).status_code == 403
