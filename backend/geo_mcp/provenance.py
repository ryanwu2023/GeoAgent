"""Snapshot provenance helpers for MCP responses."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timedelta

from backend.catalog import RULE_VERSION


def snapshot_provenance(snapshot: dict[str, Any], *, working_snapshot_id: str | None, latest_snapshot_id: str | None) -> dict[str, Any]:
    snapshot_id = snapshot.get("id")
    # Match research.answer: relative weeks are anchored to the selected
    # snapshot import date, not the Agent's wall clock or latest snapshot.
    try:
        anchor = datetime.fromisoformat(str(snapshot.get("created_at", "")).replace("Z", "+00:00")).date()
        week = {"start": (anchor - timedelta(days=anchor.weekday())).isoformat(), "end": anchor.isoformat()}
    except ValueError:
        week = None
    return {
        "snapshot_id": snapshot_id,
        "snapshot_created_at": snapshot.get("created_at"),
        "relative_time_basis": {
            "basis": "未另行指定时，本周以所选快照导入日期为基准，从该周周一至导入日；不是今天所在周，不自动切换 latest。日期比较需明确首末观测日，单期变化不能冒充整周变化。",
            "this_week": week,
        },
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
