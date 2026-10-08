"""Minimal structured audit logging for MCP requests."""

from __future__ import annotations

import hashlib
import logging
import uuid
from time import monotonic
from typing import Any

logger = logging.getLogger("geoagent.mcp.audit")


def event_id(request_id: str, tool: str) -> str:
    return "geo-audit-" + hashlib.sha256(f"{request_id}:{tool}".encode()).hexdigest()[:20]


def request_id_from_context(ctx: Any) -> str:
    try:
        return str(ctx.request_id)
    except Exception:
        return f"local-{uuid.uuid4().hex}"


def record(*, request_id: str, principal_id: str, agent_id: str, tool: str, snapshot_id: str | None, status: str, started: float) -> str:
    audit_event_id = event_id(request_id, tool)
    logger.info(
        "mcp_audit request_id=%s principal_id=%s agent_id=%s tool=%s snapshot_id=%s status=%s duration_ms=%d audit_event_id=%s",
        request_id,
        principal_id,
        agent_id,
        tool,
        snapshot_id or "",
        status,
        round((monotonic() - started) * 1000),
        audit_event_id,
    )
    return audit_event_id
