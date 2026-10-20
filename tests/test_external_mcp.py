import asyncio
import json

import pytest

from backend import external_mcp


class FakeClient:
    def __init__(self, config):
        self.config = config

    async def connect(self):
        return {"serverInfo": {"name": "test-server", "version": "1"}}

    async def list_tools(self):
        return [{"name": "knowledge_search"}, {"name": "service_status"}]


def test_test_success_is_required_before_server_is_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(external_mcp, "McpClient", FakeClient)

    result = asyncio.run(
        external_mcp.test_and_save(None, "research", "http", "https://example.org/mcp", "secret")
    )

    assert result["ok"] is True
    assert result["server"]["last_test"]["tool_count"] == 2
    assert "authorization" not in json.dumps(result)
    saved = json.loads((tmp_path / "mcp-servers.json").read_text(encoding="utf-8"))
    assert saved["servers"][0]["authorization"] == "Bearer secret"


def test_failed_test_does_not_replace_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(external_mcp, "McpClient", FakeClient)
    first = asyncio.run(
        external_mcp.test_and_save(None, "research", "http", "https://example.org/mcp", "old-key")
    )
    before = (tmp_path / "mcp-servers.json").read_text(encoding="utf-8")

    class FailingClient(FakeClient):
        async def connect(self):
            raise ValueError("secret must never be reflected")

    monkeypatch.setattr(external_mcp, "McpClient", FailingClient)
    with pytest.raises(ValueError):
        asyncio.run(
            external_mcp.test_and_save(
                first["server"]["id"], "changed", "http", "https://example.net/mcp", "new-key"
            )
        )
    assert (tmp_path / "mcp-servers.json").read_text(encoding="utf-8") == before


def test_edit_can_reuse_key_and_delete_keeps_public_response_secret_free(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(external_mcp, "McpClient", FakeClient)
    first = asyncio.run(
        external_mcp.test_and_save(None, "first", "http", "https://example.org/mcp", "token")
    )
    server_id = first["server"]["id"]
    asyncio.run(
        external_mcp.test_and_save(server_id, "renamed", "http", "https://example.org/v2/mcp", "")
    )
    saved = json.loads((tmp_path / "mcp-servers.json").read_text(encoding="utf-8"))["servers"][0]
    assert saved["authorization"] == "Bearer token"
    assert saved["name"] == "renamed"
    assert external_mcp.delete_server(server_id)["servers"] == []


def test_legacy_single_server_is_migrated_without_deleting_source(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    legacy = {
        "name": "legacy",
        "url": "https://example.org/mcp",
        "authorization": "Bearer old",
        "verify_tls": True,
    }
    (tmp_path / "mcp-config.json").write_text(json.dumps(legacy), encoding="utf-8")
    public = external_mcp.public_registry()
    assert public["servers"][0]["name"] == "legacy"
    assert public["servers"][0]["configured"] is True
    assert "authorization" not in json.dumps(public)
    assert (tmp_path / "mcp-config.json").exists()


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/mcp",
        "https://user:password@example.org/mcp",
        "https://example.org/mcp?key=secret",
        "https://example.org/mcp#secret",
    ],
)
def test_unsafe_urls_are_rejected(tmp_path, monkeypatch, url):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        external_mcp.prepare_draft(None, "server", "http", url, "key")
