"""Snapshot provenance helpers for MCP responses."""

from __future__ import annotations

from typing import Any

from backend.catalog import RULE_VERSION


def snapshot_provenance(snapshot: dict[str, Any], *, working_snapshot_id: str | None, latest_snapshot_id: str | None) -> dict[str, Any]:
    snapshot_id = snapshot.get("id")
    return {
        "snapshot_id": snapshot_id,
        "snapshot_created_at": snapshot.get("created_at"),
        "is_working_snapshot": bool(snapshot_id and snapshot_id == working_snapshot_id),
        "is_latest_snapshot": bool(snapshot_id and snapshot_id == latest_snapshot_id),
        "rule_version": RULE_VERSION,
        "source": "GeoAgent research.sqlite3",
    }


def list_provenance(*, working_snapshot_id: str | None, latest_snapshot_id: str | None) -> dict[str, Any]:
    return {
        "working_snapshot_id": working_snapshot_id,
        "latest_snapshot_id": latest_snapshot_id,
        "rule_version": RULE_VERSION,
        "source": "GeoAgent research.sqlite3",
    }
