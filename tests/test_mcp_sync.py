import base64
from dataclasses import replace
import hashlib
import json

import pytest
from starlette.testclient import TestClient

from backend import store
from backend.geo_mcp import sync
from backend.geo_mcp.auth import AuthConfig
from backend.geo_mcp.contracts import MCPError, Principal
from backend.geo_mcp.server import create_app


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    original_get = store.get_snapshot
    original_get.cache_clear()
    yield tmp_path
    original_get.cache_clear()


@pytest.fixture
def principal():
    all_scope = frozenset({"*"})
    return Principal("customer", "sync", "shared", all_scope, all_scope, all_scope,
                     frozenset({"geo_sync_manifest", "geo_sync_chunk"}))


def seed(text="公开报道", label="a"):
    payload = {
        "records": [{"id": label, "title": "事件", "text": text,
                     "url": "https://example.org/news?token=secret&utm_source=test",
                     "topics": ["usiran"], "dimensions": {"usiran": "diplomacy"},
                     "points": [{"lat": 35, "lon": 51, "role": "文章提及", "secret": "hidden"}],
                     "raw_refs": ["/private/source.json"], "local_password": "hidden"}],
        "observations": [{"id": "metric", "name": "指标", "source_ids": [label],
                          "history": [{"date": "2026-10-01", "value": 0, "secret": "hidden"},
                                      {"date": "2026-10-02", "value": None}],
                          "private_path": "/private/metric.json"}],
        "sources": [{"id": "source", "name": "来源", "error": "Bearer secret"}],
        "projects": [{"id": "project", "documents": [{"archive": "private.json"}]}],
        "manifests": [{"archive": "private.json"}], "settings": {"token": "hidden"},
        "errors": [], "cleaning": {},
    }
    return store.save_snapshot(payload)[0]


def download(principal, info):
    offset = 0
    chunks = []
    while True:
        page = sync.read_chunk(principal, info["snapshot_id"], offset)
        chunk = base64.b64decode(page["content"], validate=True)
        assert len(chunk) <= sync.CHUNK_BYTES
        assert len(chunk) == page["returned_bytes"]
        assert hashlib.sha256(chunk).hexdigest() == page["chunk_sha256"]
        assert page["sha256"] == info["sha256"]
        chunks.append(chunk)
        if not page["has_more"]:
            assert page["next_offset"] is None
            break
        offset = page["next_offset"]
        assert offset == sum(map(len, chunks))
    data = b"".join(chunks)
    assert len(data) == info["total_bytes"]
    assert hashlib.sha256(data).hexdigest() == info["sha256"]
    return json.loads(data)


def test_lossless_chunked_export_and_no_private_data(database, principal):
    original = "多字节新闻正文🙂" * 40000
    sid = seed(original)
    info = sync.manifest(principal)
    assert info["snapshot_id"] == sid and info["total_bytes"] > sync.CHUNK_BYTES * 2
    data = download(principal, info)
    assert data["source_snapshot_id"] == sid
    assert data["records"][0]["text"] == original
    assert data["records"][0]["url"] == "https://example.org/news"
    assert data["records"][0]["points"] == [{"lat": 35, "lon": 51, "role": "文章提及"}]
    assert data["observations"][0]["source_ids"] == ["a"]
    assert [p["value"] for p in data["observations"][0]["history"]] == [0, None]
    assert data["projects"][0]["documents"] == []
    assert not any(word in json.dumps(data) for word in ("hidden", "private", "secret", "manifests"))
    assert store.get_snapshot(sid)["records"][0]["raw_refs"]  # immutable input untouched
    assert sync.manifest(principal, known_sha256=info["sha256"])["unchanged"]


def test_snapshot_pinning_and_resume_after_new_import(database, principal, monkeypatch):
    old = seed("x" * (sync.CHUNK_BYTES * 2))
    info = sync.manifest(principal)
    first = sync.read_chunk(principal, old)
    new = seed("replacement", "b")
    newer = sync.manifest(principal, known_sha256=info["sha256"])
    assert newer["snapshot_id"] == new and not newer["unchanged"]
    assert sync.read_chunk(principal, old, first["next_offset"])["sha256"] == info["sha256"]
    assert download(principal, info)["records"][0]["id"] == "a"
    # Cached reads do not decode/serialize the whole snapshot for every chunk.
    monkeypatch.setattr(store, "get_snapshot", lambda _: pytest.fail("cache should be reused"))
    assert sync.manifest(principal, old)["sha256"] == info["sha256"]
    assert sync.read_chunk(principal, old)["content"] == first["content"]


def test_empty_database_does_not_publish_demo(database, principal):
    assert sync.manifest(principal) == {"available": False, "schema_version": sync.FORMAT}


def test_scope_checked_on_every_chunk_and_latest_is_visible_only(database, principal):
    old = seed()
    new = seed("new", "b")
    info = sync.manifest(principal, old)  # warm the cache with broad credentials
    restricted = replace(principal, allowed_snapshot_ids=frozenset({old}))
    assert sync.manifest(restricted)["snapshot_id"] == old
    with pytest.raises(MCPError, match="GEO_SCOPE_DENIED"):
        sync.read_chunk(restricted, new)
    for limited in (replace(principal, allowed_topics=frozenset({"usiran"})),
                    replace(principal, allowed_dimensions=frozenset({"diplomacy"}))):
        with pytest.raises(MCPError, match="GEO_SCOPE_DENIED"):
            sync.manifest(limited, old)
        with pytest.raises(MCPError, match="GEO_SCOPE_DENIED"):
            sync.read_chunk(limited, old)
    working_only = replace(principal, allowed_snapshot_ids=frozenset({"working"}))
    store.set_working_snapshot_id(old)
    assert sync.manifest(working_only)["sha256"] == info["sha256"]
    store.set_working_snapshot_id(new)
    with pytest.raises(MCPError, match="GEO_SCOPE_DENIED"):
        sync.read_chunk(working_only, old)
    with pytest.raises(MCPError, match="GEO_TOOL_NOT_ALLOWED"):
        sync.manifest(replace(principal, allowed_tools=frozenset({"geo_snapshot_list"})))


def test_invalid_offsets_and_missing_snapshot(database, principal):
    sid = seed()
    size = sync.manifest(principal)["total_bytes"]
    for offset in (-1, size, size + 1, True, 1.5):
        with pytest.raises(MCPError, match="GEO_INVALID_OFFSET"):
            sync.read_chunk(principal, sid, offset)
    with pytest.raises(MCPError, match="GEO_SNAPSHOT_REQUIRED"):
        sync.read_chunk(principal, "")
    with pytest.raises(MCPError, match="GEO_SNAPSHOT_NOT_FOUND"):
        sync.manifest(principal, "../../missing")


def test_interrupted_cache_publish_is_rebuilt(database, principal, monkeypatch):
    seed()
    real_replace = sync.os.replace
    def fail_metadata(source, target):
        if str(target).endswith(".manifest.json"):
            raise OSError("simulated crash before metadata publish")
        real_replace(source, target)
    monkeypatch.setattr(sync.os, "replace", fail_metadata)
    with pytest.raises(OSError):
        sync.manifest(principal)
    root = database / "mcp-exports"
    assert not list(root.glob("*.manifest.json"))
    assert not list(root.glob("tmp*"))
    monkeypatch.setattr(sync.os, "replace", real_replace)
    info = sync.manifest(principal)
    assert download(principal, info)["records"][0]["id"] == "a"
    # Partial metadata from an interrupted/invalid cache is recoverable too.
    next(root.glob("*.manifest.json")).write_text('{"unfinished":', encoding="utf-8")
    assert sync.manifest(principal)["sha256"] == info["sha256"]


def test_managed_key_revocation_blocks_cached_chunks(database):
    from backend.integrations import NewKey, create_key, revoke_key
    sid = seed()
    key = create_key(NewKey(label="customer-test"))
    config = AuthConfig(token_hash=None, principal=None)
    with TestClient(create_app(auth_config=config, managed_keys=True), base_url="http://127.0.0.1:8401") as client:
        headers = {"Authorization": "Bearer " + key["key"], "Accept": "application/json, text/event-stream"}
        def call(name):
            return client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": {"snapshot_id": sid}},
            })
        response = call("geo_sync_manifest")
        assert response.status_code == 200
        assert json.loads(response.json()["result"]["content"][0]["text"])["ok"]
        revoke_key(key["id"])
        assert call("geo_sync_chunk").status_code == 401


def test_mcp_wire_discovery_bearer_auth_and_download(database, principal):
    sid = seed()
    config = AuthConfig(token_hash=hashlib.sha256(b"customer-key").hexdigest(), principal=principal)
    with TestClient(create_app(auth_config=config), base_url="http://127.0.0.1:8401") as client:
        headers = {"Authorization": "Bearer customer-key", "Accept": "application/json, text/event-stream"}
        def rpc(method, params):
            response = client.post("/mcp", headers=headers,
                                   json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            assert response.status_code == 200
            return response.json()["result"]
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post("/mcp", headers={**headers, "Authorization": "Bearer wrong"}, json={}).status_code == 401
        rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "sync-test", "version": "1"}})
        names = {tool["name"] for tool in rpc("tools/list", {})["tools"]}
        assert {"geo_sync_manifest", "geo_sync_chunk"} <= names
        def call(name, arguments):
            result = rpc("tools/call", {"name": name, "arguments": arguments})
            return json.loads(result["content"][0]["text"])
        manifest = call("geo_sync_manifest", {})
        assert manifest["ok"] and manifest["data"]["snapshot_id"] == sid
        page = call("geo_sync_chunk", {"snapshot_id": sid})
        assert page["ok"]
        assert hashlib.sha256(base64.b64decode(page["data"]["content"])).hexdigest() == manifest["data"]["sha256"]
        assert call("geo_sync_chunk", {"snapshot_id": sid, "offset": -1})["error"]["code"] == "GEO_INVALID_OFFSET"
