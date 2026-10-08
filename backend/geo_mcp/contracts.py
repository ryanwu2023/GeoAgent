"""Stable contracts shared by the GeoAgent MCP tools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = "geo-mcp/v1"
MAX_PAGE_SIZE = 50
MAX_TEXT_BYTES = 12_000


@dataclass(frozen=True)
class Principal:
    principal_id: str
    agent_id: str
    geo_workspace_id: str
    allowed_snapshot_ids: frozenset[str]
    allowed_topics: frozenset[str]
    allowed_dimensions: frozenset[str]
    allowed_tools: frozenset[str]
    read_only: bool = True


@dataclass(frozen=True)
class MCPError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def as_dict(self, *, request_id: str | None = None, tool: str | None = None) -> dict[str, Any]:
        return {
            "ok": False,
            "request_id": request_id,
            "tool": tool,
            "error": {"code": self.code, "message": self.message, "details": self.details or {}},
            "protocol_version": PROTOCOL_VERSION,
        }


def success_envelope(
    *,
    data: Any,
    request_id: str,
    tool: str,
    provenance: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    audit_event_id: str | None = None,
) -> str:
    """Return JSON text so every client receives the same wire-level envelope."""

    return json.dumps(
        {
            "ok": True,
            "request_id": request_id,
            "tool": tool,
            "data": data,
            "provenance": provenance or {},
            "warnings": warnings or [],
            "audit_event_id": audit_event_id,
            "protocol_version": PROTOCOL_VERSION,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def error_text(error: MCPError, *, request_id: str, tool: str) -> str:
    return json.dumps(error.as_dict(request_id=request_id, tool=tool), ensure_ascii=False, separators=(",", ":"))


def clamp_limit(value: int | None, *, maximum: int = MAX_PAGE_SIZE) -> int:
    if value is None:
        return min(20, maximum)
    if value < 1 or value > maximum:
        raise MCPError("GEO_LIMIT_EXCEEDED", f"limit 必须在 1 到 {maximum} 之间", {"maximum": maximum})
    return value
