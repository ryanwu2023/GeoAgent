"""Snapshot provenance helpers for MCP responses."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timedelta

from backend.catalog import RULE_VERSION


def collection_snapshot_boundary() -> dict[str, str]:
    """Shared material-lineage semantics, not a verdict on collection success."""
    return {
        "collection_recovery": "采集恢复不等于补齐旧快照；采集成功、导入成功和目标范围覆盖补齐是不同状态。",
        "historical_material": "旧快照原始归档材料不会因采集器修复或后续采集自动回填；复核、翻译等展示投影更新不等于新增归档材料。",
        "verification": "后续材料须确认导入状态及对应snapshot_id，再读取该快照同主题、维度、日期的指标或证据核对缺口；未读取不能断言覆盖补齐、完整一周或可比指标增多。",
    }


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
        "collection_snapshot_boundary": collection_snapshot_boundary(),
        "relative_time_basis": {
            "basis": "未另行指定时，本周以所选快照导入日期为基准，从该周周一至导入日；不是今天所在周，不自动切换 latest。日期比较需明确首末观测日，单期变化不能冒充整周变化。",
            "this_week": week,
        },
        "is_working_snapshot": bool(snapshot_id and snapshot_id == working_snapshot_id),
        "is_latest_snapshot": bool(snapshot_id and snapshot_id == latest_snapshot_id),
        "rule_version": RULE_VERSION,
        "source": "GeoAgent PostgreSQL",
    }


def list_provenance(*, working_snapshot_id: str | None, latest_snapshot_id: str | None) -> dict[str, Any]:
    return {
        "working_snapshot_id": working_snapshot_id,
        "latest_snapshot_id": latest_snapshot_id,
        "rule_version": RULE_VERSION,
        "source": "GeoAgent PostgreSQL",
    }
