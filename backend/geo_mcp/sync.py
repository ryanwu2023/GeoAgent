"""Immutable, bounded MCP exports for customer-controlled synchronization.

Only imported snapshot data is exported. Credentials, reviews, conversations,
raw archives and runtime logs never enter this projection. Bump FORMAT when
changing the projection: cached exports are part of the public wire contract.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Annotated
from mcp.server.fastmcp import Context
from pydantic import Field

from backend import store
from backend.adapters import safe_url
from .contracts import MCPError
from .scope import authorize

FORMAT = "geo-sync/v1"
CHUNK_BYTES = 256 * 1024
_build_lock = Lock()
SyncSnapshot = Annotated[str, Field(max_length=256, description="冻结快照ID；manifest可留空选择最新可见已导入版本，chunk必须固定使用manifest返回的ID。")]
KnownHash = Annotated[str, Field(max_length=64, description="客户上次已完整校验并入库的SHA256；首次同步留空。仅相同时unchanged=true。")]
ByteOffset = Annotated[int, Field(description="原始JSON文件的字节偏移，首次为0；断点续传用上次next_offset，不是字符偏移。")]

# Explicit public fields; new internal fields cannot silently become exports.
RECORD_FIELDS = (
    "id", "source_id", "source_name", "tier", "publisher", "title", "text",
    "summary", "original_title", "title_zh", "summary_zh", "published_at",
    "fetched_at", "first_seen", "event_at", "party", "nature", "verification",
    "classification", "scope", "origin_id", "supplemental", "event_category",
    "situation_signal", "signal_basis", "upstream_kind",
)
OBSERVATION_FIELDS = (
    "id", "series", "name", "type", "unit", "frequency", "scope", "topic",
    "dimension", "bucket", "method", "boundary", "observed_at", "quality_issue",
)
SOURCE_FIELDS = (
    "id", "monitor", "name", "state", "count", "latest", "checked_at", "tier", "http_status",
)
PROJECT_FIELDS = (
    "id", "name", "state", "records", "topic_records", "missing_published",
    "missing_fetched", "failed_sources", "sources",
)


def _scalars(item, fields):
    return {key: item[key] for key in fields if key in item
            and (item[key] is None or isinstance(item[key], (str, int, float, bool)))}


def _strings(items):
    return [item for item in items if isinstance(item, str)] if isinstance(items, list) else []


def _record(item):
    result = _scalars(item, RECORD_FIELDS)
    result["url"] = safe_url(item.get("url") or item.get("link"))
    for key in ("topics", "adapters", "crawler_buckets"):
        result[key] = _strings(item.get(key))
    result["dimensions"] = _scalars(item.get("dimensions") or {}, result["topics"])
    result["dimension_candidates"] = {
        topic: _strings((item.get("dimension_candidates") or {}).get(topic))
        for topic in result["topics"]
    }
    result["points"] = [_scalars(p, ("lat", "lon", "label", "precision", "role", "evidence_text"))
                        for p in item.get("points", []) if isinstance(p, dict)]
    # The receiving UI must not expose broken links into the publisher's disk.
    result["raw_refs"] = []
    return result


def projection(snapshot):
    observations = []
    for item in snapshot.get("observations", []):
        row = _scalars(item, OBSERVATION_FIELDS)
        row["source_ids"] = _strings(item.get("source_ids"))
        row["history"] = [_scalars(p, ("date", "value"))
                          for p in item.get("history", []) if isinstance(p, dict)]
        observations.append(row)
    return {
        "schema_version": FORMAT,
        "source_snapshot_id": snapshot["id"],
        "source_created_at": snapshot["created_at"],
        "records": [_record(item) for item in snapshot.get("records", [])],
        "observations": observations,
        "sources": [_scalars(item, SOURCE_FIELDS) for item in snapshot.get("sources", [])],
        "projects": [{**_scalars(item, PROJECT_FIELDS), "documents": []}
                     for item in snapshot.get("projects", [])],
        "boundary": "采集并导入的冻结数据；不是实时采集状态或人工核验结论。不含原始附件、翻译缓存、客户配置、审核及问答记录。",
    }


def _select(principal, tool, snapshot_id):
    authorize(principal, tool=tool)
    # Export is deliberately whole-snapshot, not a filtered research response.
    # Never let restricted credentials read other topics via an export/cache.
    if "*" not in principal.allowed_topics or "*" not in principal.allowed_dimensions:
        raise MCPError("GEO_SCOPE_DENIED", "完整数据同步需要全部主题和维度授权")
    working = store.get_working_snapshot_id()
    if not snapshot_id:
        allowed = principal.allowed_snapshot_ids
        rows = sorted(store.snapshots(), key=lambda r: (r["created_at"], r["id"]), reverse=True)
        snapshot_id = next((r["id"] for r in rows if "*" in allowed or r["id"] in allowed
                            or ("working" in allowed and r["id"] == working)), None)
        if snapshot_id is None:
            return None
    authorize(principal, tool=tool, snapshot_id=snapshot_id, working_snapshot_id=working)
    with store.db() as connection:
        row = connection.execute("SELECT id FROM snapshots WHERE id=%s", (snapshot_id,)).fetchone()
    if row is None:
        raise MCPError("GEO_SNAPSHOT_NOT_FOUND", "同步快照不存在")
    return snapshot_id


def _artifact(snapshot_id):
    # Hash the identifier rather than using client input as a filesystem path.
    key = hashlib.sha256((FORMAT + ":" + snapshot_id).encode()).hexdigest()
    root = store.data_dir() / "mcp-exports"
    target, meta = root / (key + ".json"), root / (key + ".manifest.json")

    def cached():
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            if (info["schema_version"] == FORMAT and info["snapshot_id"] == snapshot_id
                    and target.stat().st_size == info["total_bytes"]):
                return target, info
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Interrupted or damaged cache metadata can be regenerated.
        return None

    existing = cached()
    if existing:
        return existing
    with _build_lock:
        existing = cached()
        if existing:
            return existing
        payload = projection(store.get_snapshot(snapshot_id))
        root.mkdir(parents=True, exist_ok=True)
        temporary_paths = []
        try:
            sha = hashlib.sha256()
            total = 0
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as output:
                temporary_paths.append(Path(output.name))
                encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                for part in encoder.iterencode(payload):
                    chunk = part.encode("utf-8")
                    sha.update(chunk)
                    total += len(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            info = {
                "schema_version": FORMAT, "snapshot_id": snapshot_id,
                "source_created_at": payload["source_created_at"],
                "sha256": sha.hexdigest(), "total_bytes": total,
                "chunk_bytes": CHUNK_BYTES, "encoding": "base64", "media_type": "application/json",
                "counts": {key: len(payload[key]) for key in ("records", "observations", "sources", "projects")},
            }
            with tempfile.NamedTemporaryFile(dir=root, mode="w", encoding="utf-8", delete=False) as output:
                temporary_paths.append(Path(output.name))
                json.dump(info, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            # Concurrent processes build identical bytes for the same immutable
            # snapshot. Metadata is published only after the completed data file.
            os.replace(temporary_paths[0], target)
            os.replace(temporary_paths[1], meta)
            return target, info
        finally:
            for path in temporary_paths:
                path.unlink(missing_ok=True)


def manifest(principal, snapshot_id="", known_sha256=""):
    sid = _select(principal, "geo_sync_manifest", snapshot_id)
    if sid is None:
        return {"available": False, "schema_version": FORMAT}
    _, info = _artifact(sid)
    return {**info, "available": True, "unchanged": known_sha256 == info["sha256"]}


def read_chunk(principal, snapshot_id, offset=0):
    if not snapshot_id:
        raise MCPError("GEO_SNAPSHOT_REQUIRED", "分块下载必须固定 manifest 返回的 snapshot_id")
    sid = _select(principal, "geo_sync_chunk", snapshot_id)
    path, info = _artifact(sid)
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset < info["total_bytes"]:
        raise MCPError("GEO_INVALID_OFFSET", "offset 必须在数据文件字节范围内")
    with path.open("rb") as stream:
        stream.seek(offset)
        chunk = stream.read(CHUNK_BYTES)
    next_offset = offset + len(chunk)
    return {
        "schema_version": FORMAT, "snapshot_id": sid, "sha256": info["sha256"],
        "total_bytes": info["total_bytes"], "offset": offset, "returned_bytes": len(chunk),
        "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
        "encoding": "base64", "content": base64.b64encode(chunk).decode("ascii"),
        "next_offset": next_offset if next_offset < info["total_bytes"] else None,
        "has_more": next_offset < info["total_bytes"],
    }


def register_sync_tools(server, run_tool):
    @server.tool(name="geo_sync_manifest", description="客户数据同步：发现最新可见的已导入快照（不等于当前工作快照）。也可指定历史snapshot_id。用known_sha256判断是否变化；返回总大小、校验和与分块规格。无快照时available=false，不自动采集或返回演示数据。完整导出要求全部主题/维度授权。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_sync_manifest(ctx: Context, snapshot_id: SyncSnapshot = "", known_sha256: KnownHash = "") -> str:
        def handler(principal, _):
            result = manifest(principal, snapshot_id, known_sha256)
            return result, {"snapshot_id": result.get("snapshot_id"), "kind": "snapshot-export"}, []
        return await run_tool(ctx=ctx, tool="geo_sync_manifest", handler=handler)

    @server.tool(name="geo_sync_chunk", description="客户机器同步：按manifest固定的snapshot_id和字节offset读取最多256KiB数据。base64解码后顺序拼接，验证每块chunk_sha256及全量sha256，通过后才入库；不是给模型阅读的摘要。按next_offset断点续传，has_more=false结束。每次调用仍检查授权。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_sync_chunk(snapshot_id: SyncSnapshot, ctx: Context, offset: ByteOffset = 0) -> str:
        def handler(principal, _):
            result = read_chunk(principal, snapshot_id, offset)
            return result, {"snapshot_id": snapshot_id, "kind": "snapshot-export"}, []
        return await run_tool(ctx=ctx, tool="geo_sync_chunk", handler=handler)
