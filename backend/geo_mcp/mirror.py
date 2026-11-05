"""Lossless snapshot mirror for a GeoAgent deployment without overseas egress.

Unlike the customer projection in ``sync.py``, a mirror carries the stored
snapshot payload unchanged, so the receiver re-derives the same content-hash
snapshot id and can prove the bytes were not altered. Translations live in a
separate cache table; only rows for texts of this snapshot are included.
Bump FORMAT when changing the document shape.
"""
from __future__ import annotations

import gzip
import json

from mcp.server.fastmcp import Context
from pydantic import Field

from backend import store
from .contracts import MCPError
from .sync import SyncSnapshot, KnownHash, ByteOffset, _select, build_artifact, chunk_response

FORMAT = "geo-mirror/v1"
TRANSLATED_FIELDS = ("title", "text", "summary", "original_title", "source_name", "publisher")


def translatable_texts(payload):
    """Texts whose translations a receiver may need, keyed by translation hash."""
    texts = {}
    for record in payload.get("records", []):
        for field in TRANSLATED_FIELDS:
            value = record.get(field)
            if isinstance(value, str) and value:
                texts[store.digest(value)] = value
    return texts


def stored_payload(snapshot_id):
    """Return the exact payload whose digest is the snapshot id."""
    with store.db() as connection:
        row = connection.execute("SELECT payload,created_at FROM snapshots WHERE id=%s", (snapshot_id,)).fetchone()
    if row is None:
        raise MCPError("GEO_SNAPSHOT_NOT_FOUND", "同步快照不存在")
    payload = json.loads(row["payload"])
    payload.pop("import_stats", None)  # added by save_snapshot after hashing
    if store.digest(payload) != snapshot_id:
        raise MCPError("GEO_MIRROR_UNVERIFIABLE", "该快照载荷无法复算快照ID，不能镜像", {"snapshot_id": snapshot_id})
    return payload, row["created_at"]


def mirror_document(snapshot_id):
    payload, created_at = stored_payload(snapshot_id)
    texts = translatable_texts(payload)
    translations = []
    if texts:
        with store.db() as connection:
            rows = connection.execute(
                "SELECT hash,chinese,method FROM translations WHERE hash = ANY(%s) ORDER BY hash", (list(texts),)
            ).fetchall()
        translations = [{"hash": r["hash"], "chinese": r["chinese"], "method": r["method"]} for r in rows]
    return {
        "schema_version": FORMAT, "snapshot_id": snapshot_id, "source_created_at": created_at,
        "payload": payload, "translations": translations,
    }


def _render(snapshot_id):
    document = mirror_document(snapshot_id)
    # mtime=0 keeps the compressed bytes, and therefore sha256, stable per snapshot.
    data = gzip.compress(store.canonical(document).encode("utf-8"), mtime=0)
    return iter((data,)), {
        "source_created_at": document["source_created_at"], "encoding": "base64", "media_type": "application/gzip",
        "counts": {"records": len(document["payload"].get("records", [])),
                   "observations": len(document["payload"].get("observations", [])),
                   "translations": len(document["translations"])},
    }


def manifest(principal, snapshot_id="", known_sha256=""):
    sid = _select(principal, "geo_mirror_manifest", snapshot_id)
    if sid is None:
        return {"available": False, "schema_version": FORMAT}
    _, info = build_artifact(sid, FORMAT, _render)
    return {**info, "available": True, "unchanged": known_sha256 == info["sha256"]}


def read_chunk(principal, snapshot_id, offset=0):
    if not snapshot_id:
        raise MCPError("GEO_SNAPSHOT_REQUIRED", "分块下载必须固定 manifest 返回的 snapshot_id")
    sid = _select(principal, "geo_mirror_chunk", snapshot_id)
    path, info = build_artifact(sid, FORMAT, _render)
    return chunk_response(path, info, offset)


def register_mirror_tools(server, run_tool):
    @server.tool(name="geo_mirror_manifest", description="GeoAgent部署间快照镜像：返回最新可见已导入快照（或指定snapshot_id）的geo-mirror/v1无损镜像规格。内容为gzip压缩JSON，含原始快照载荷与相关译文，接收方用载荷复算快照ID校验。不是给模型阅读的摘要；不触发采集。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_mirror_manifest(ctx: Context, snapshot_id: SyncSnapshot = "", known_sha256: KnownHash = "") -> str:
        def handler(principal, _):
            result = manifest(principal, snapshot_id, known_sha256)
            return result, {"snapshot_id": result.get("snapshot_id"), "kind": "snapshot-mirror"}, []
        return await run_tool(ctx=ctx, tool="geo_mirror_manifest", handler=handler)

    @server.tool(name="geo_mirror_chunk", description="GeoAgent部署间快照镜像：按manifest固定的snapshot_id和字节offset读取最多256KiB gzip数据，校验chunk_sha256与全量sha256后解压入库；按next_offset断点续传。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_mirror_chunk(snapshot_id: SyncSnapshot, ctx: Context, offset: ByteOffset = 0) -> str:
        def handler(principal, _):
            result = read_chunk(principal, snapshot_id, offset)
            return result, {"snapshot_id": snapshot_id, "kind": "snapshot-mirror"}, []
        return await run_tool(ctx=ctx, tool="geo_mirror_chunk", handler=handler)
