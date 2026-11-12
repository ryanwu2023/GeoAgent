"""采集管线启动期维护：功能开关与内置采集器同步。

管线不单独定时（2026-11-11 用户决定）：启用的管线随现有整批采集运行，这里不再有调度线程。
"""
from __future__ import annotations

import logging
import os

import psycopg

from backend import collector_config
from backend.pipeline_service import effective_builtin_enabled
from backend.store import db

log = logging.getLogger(__name__)

BUILTIN_CONFIG = '{"schema_version": "pipeline/1", "builtin": true}'


def pipelines_enabled() -> bool:
    return os.getenv("GEO_PIPELINES_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def sync_builtin_pipelines(registry: list[dict]) -> None:
    """为注册表中的采集器建或刷新 builtin 行：enabled 取有效状态（注册表启用且不在排除列表）。

    名称、主题等已存在的字段不覆盖；与用户管线重名则跳过并告警。
    """
    exclude_ids = collector_config.load_config().get("exclude_ids", [])
    for item in registry:
        topics = list(item.get("topics") or [])
        if not topics:
            log.warning("内置采集器 %s 未声明主题，已跳过同步", item.get("id"))
            continue
        enabled = effective_builtin_enabled(item, exclude_ids)
        try:
            with db() as conn:
                row = conn.execute(
                    "INSERT INTO collection_pipelines(id,kind,name,description,topics,enabled,current_version,owner,created_by) "
                    "VALUES (%s,'builtin',%s,'',%s,%s,1,'system','system') "
                    "ON CONFLICT (id) DO UPDATE SET enabled=EXCLUDED.enabled "
                    "WHERE collection_pipelines.kind='builtin' AND collection_pipelines.enabled IS DISTINCT FROM EXCLUDED.enabled "
                    "RETURNING (xmax = 0) AS inserted",
                    (item["id"], item["name_zh"], topics, enabled)).fetchone()
                if row and row["inserted"]:
                    conn.execute(
                        "INSERT INTO collection_pipeline_versions(pipeline_id,version,config,change_note,source_channel,created_by) "
                        "VALUES (%s,1,%s,'内置采集器同步','migration','system')", (item["id"], BUILTIN_CONFIG))
        except psycopg.errors.UniqueViolation:
            log.warning("内置采集器 %s 与已有管线重名（%s），已跳过", item["id"], item["name_zh"])
