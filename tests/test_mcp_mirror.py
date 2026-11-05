import base64
import gzip
import hashlib
import json

import pytest

from backend import store
from backend.chinese import save_translation
from backend.geo_mcp import mirror, sync
from backend.geo_mcp.contracts import MCPError, Principal


@pytest.fixture
def principal():
    all_scope = frozenset({"*"})
    return Principal("hft", "mirror", "shared", all_scope, all_scope, all_scope, all_scope)


def seed(text="Iran talks resume", label="a"):
    payload = {
        "records": [{"id": label, "title": "Talks", "text": text, "source_name": "Reuters",
                     "raw_refs": ["/private/source.json"], "topics": ["usiran"]}],
        "observations": [{"id": "metric", "name": "指标"}],
        "sources": [], "projects": [], "errors": [], "cleaning": {"kept": 1},
    }
    return store.save_snapshot(payload)[0]


def download(principal, info):
    offset, chunks = 0, []
    while True:
        page = mirror.read_chunk(principal, info["snapshot_id"], offset)
        chunk = base64.b64decode(page["content"], validate=True)
        assert hashlib.sha256(chunk).hexdigest() == page["chunk_sha256"]
        chunks.append(chunk)
        if not page["has_more"]:
            break
        offset = page["next_offset"]
    data = b"".join(chunks)
    assert hashlib.sha256(data).hexdigest() == info["sha256"]
    return json.loads(gzip.decompress(data))


def test_mirror_is_lossless_and_carries_snapshot_translations(principal):  # RC-07
    sid = seed("Iran talks resume " * 50000)
    save_translation("Talks", "会谈")
    save_translation("unrelated text", "无关")
    info = mirror.manifest(principal)
    assert info["available"] and info["snapshot_id"] == sid
    assert info["schema_version"] == "geo-mirror/v1" and info["media_type"] == "application/gzip"
    document = download(principal, info)
    assert store.digest(document["payload"]) == sid
    assert document["payload"]["records"][0]["raw_refs"] == ["/private/source.json"]
    assert "import_stats" not in document["payload"]
    assert [row["chinese"] for row in document["translations"]] == ["会谈"]
    assert info["counts"] == {"records": 1, "observations": 1, "translations": 1}


def test_mirror_bytes_are_stable_and_separate_from_sync(principal, tmp_path):  # RC-08
    sid = seed()
    first = mirror.manifest(principal)
    for path in (store.data_dir() / "mcp-exports").glob("*"):
        path.unlink()
    second = mirror.manifest(principal, sid, known_sha256=first["sha256"])
    assert second["sha256"] == first["sha256"] and second["unchanged"]
    assert sync.manifest(principal, sid)["schema_version"] == "geo-sync/v1"


def test_mirror_requires_full_scope_and_tool(principal):
    from dataclasses import replace
    sid = seed()
    with pytest.raises(MCPError, match="GEO_SCOPE_DENIED"):
        mirror.manifest(replace(principal, allowed_topics=frozenset({"usiran"})), sid)
    with pytest.raises(MCPError, match="GEO_TOOL_NOT_ALLOWED"):
        mirror.read_chunk(replace(principal, allowed_tools=frozenset({"geo_sync_chunk"})), sid)
    assert mirror.manifest(principal, known_sha256="x")["unchanged"] is False


def test_unverifiable_legacy_payload_is_refused(principal):
    sid = seed()
    with store.db() as connection:
        connection.execute("UPDATE snapshots SET payload=%s WHERE id=%s", (json.dumps({"records": []}), sid))
    with pytest.raises(MCPError, match="GEO_MIRROR_UNVERIFIABLE"):
        mirror.manifest(principal, sid)
