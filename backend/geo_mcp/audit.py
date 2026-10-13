"""Minimal structured audit logging for MCP requests."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any

logger = logging.getLogger("geoagent.mcp.audit")


def audit_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS mcp_call_audit (id INTEGER PRIMARY KEY, created_at TEXT, tool TEXT, status TEXT, duration_ms INTEGER, audit_event_id TEXT)")


def recent_calls():
    from backend.store import db
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with db() as conn:
        audit_table(conn)
        return [dict(row) for row in conn.execute("SELECT created_at,tool,status,duration_ms,audit_event_id FROM mcp_call_audit WHERE created_at>=? ORDER BY id DESC LIMIT 2000", (since,))]


def event_id(request_id: str, tool: str) -> str:
    # JSON-RPC IDs are scoped to a client session and may repeat. Each audit
    # row needs a distinct receipt, including retries from another connection.
    return "geo-audit-" + uuid.uuid4().hex


def request_id_from_context(ctx: Any) -> str:
    try:
        return str(ctx.request_id)
    except Exception:
        return f"local-{uuid.uuid4().hex}"


def record(*, request_id: str, principal_id: str, agent_id: str, tool: str, snapshot_id: str | None, status: str, started: float) -> str:
    audit_event_id = event_id(request_id, tool)
    duration_ms = round((monotonic() - started) * 1000)
    try:
        from backend.store import db
        with db() as conn:
            audit_table(conn)
            conn.execute("INSERT INTO mcp_call_audit(created_at,tool,status,duration_ms,audit_event_id) VALUES(?,?,?,?,?)",
                         (datetime.now(timezone.utc).isoformat(), tool, status, duration_ms, audit_event_id))
            conn.execute("DELETE FROM mcp_call_audit WHERE created_at<?", ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),))
    except Exception:
        logger.warning("MCP usage statistics could not be persisted")
    logger.info(
        "mcp_audit request_id=%s principal_id=%s agent_id=%s tool=%s snapshot_id=%s status=%s duration_ms=%d audit_event_id=%s",
        request_id,
        principal_id,
        agent_id,
        tool,
        snapshot_id or "",
        status,
        duration_ms,
        audit_event_id,
    )
    return audit_event_id
