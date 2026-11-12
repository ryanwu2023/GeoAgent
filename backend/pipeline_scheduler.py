"""采集管线调度：进程内线程按各管线自己的计划触发运行，并把内置采集器同步为 builtin 管线。"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Callable

import psycopg

from backend.collector_jobs import CollectorBusyError
from backend.pipeline_config import next_run_at
from backend.pipeline_runs import run_builtin, run_pipeline
from backend.store import db

log = logging.getLogger(__name__)

SCHEDULER_ACTOR = "scheduler"
BUILTIN_CONFIG = '{"schema_version": "pipeline/1", "builtin": true}'


def pipelines_enabled() -> bool:
    return os.getenv("GEO_PIPELINES_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def sync_builtin_pipelines(registry: list[dict]) -> None:
    """为注册表中尚无管线行的采集器建 builtin 行（停用）；已存在不覆盖，重名用户管线则跳过并告警。"""
    for item in registry:
        topics = item.get("topics") or []
        try:
            with db() as conn:
                inserted = conn.execute(
                    "INSERT INTO collection_pipelines(id,kind,name,description,topic,enabled,current_version,owner,created_by) "
                    "VALUES (%s,'builtin',%s,'',%s,false,1,'system','system') ON CONFLICT (id) DO NOTHING RETURNING id",
                    (item["id"], item["name_zh"], topics[0] if topics else "usiran")).fetchone()
                if inserted:
                    conn.execute(
                        "INSERT INTO collection_pipeline_versions(pipeline_id,version,config,change_note,source_channel,created_by) "
                        "VALUES (%s,1,%s,'内置采集器同步','migration','system')", (item["id"], BUILTIN_CONFIG))
        except psycopg.errors.UniqueViolation:
            log.warning("内置采集器 %s 与已有管线重名（%s），已跳过", item["id"], item["name_zh"])


def scheduled_builtin_ids() -> list[str]:
    with db() as conn:
        rows = conn.execute("SELECT id FROM collection_pipelines WHERE kind='builtin' AND enabled AND NOT is_deleted "
                            "ORDER BY id").fetchall()
    return [r["id"] for r in rows]


def _claim(pipeline_id: str, now: datetime) -> dict | None:
    """抢到锁、仍到期且无运行中记录时，先推进 next_run_at 并提交；返回原行，否则 None。"""
    with db() as conn:
        got = conn.execute("SELECT pg_try_advisory_xact_lock(hashtext('pipeline:'||%s)) AS ok", (pipeline_id,)).fetchone()
        if not got["ok"]:
            return None
        row = conn.execute("SELECT * FROM collection_pipelines WHERE id=%s AND enabled AND NOT is_deleted "
                           "AND next_run_at IS NOT NULL AND next_run_at <= %s", (pipeline_id, now)).fetchone()
        if row is None:
            return None
        running = conn.execute("SELECT 1 FROM collection_pipeline_runs WHERE pipeline_id=%s AND status='running' "
                               "AND NOT is_deleted LIMIT 1", (pipeline_id,)).fetchone()
        if running:
            return None
        conn.execute("UPDATE collection_pipelines SET next_run_at=%s WHERE id=%s", (next_run_at(row, now), pipeline_id))
        return dict(row)


def _restore(pipeline_id: str, claimed: dict, now: datetime) -> None:
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET next_run_at=%s WHERE id=%s", (claimed["next_run_at"], pipeline_id))


def tick(now: datetime, *, run: Callable[..., dict] = run_pipeline) -> list[str]:
    """扫描到期管线并依次运行；返回本轮实际触发的管线 id。"""
    with db() as conn:
        due = [r["id"] for r in conn.execute(
            "SELECT id FROM collection_pipelines WHERE enabled AND NOT is_deleted AND next_run_at IS NOT NULL "
            "AND next_run_at <= %s ORDER BY next_run_at, id", (now,)).fetchall()]
    triggered: list[str] = []
    for pipeline_id in due:
        try:
            claimed = _claim(pipeline_id, now)
        except Exception:
            log.exception("管线 %s 抢占调度锁失败", pipeline_id)
            continue
        if claimed is None:
            continue
        runner = run_builtin if claimed["kind"] == "builtin" else run
        try:
            runner(pipeline_id, trigger="schedule", actor=SCHEDULER_ACTOR)
            triggered.append(pipeline_id)
        except CollectorBusyError:
            log.info("采集服务忙，管线 %s 留待下个周期", pipeline_id)
            _restore(pipeline_id, claimed, now)
        except Exception:
            log.exception("管线 %s 调度运行失败", pipeline_id)
            triggered.append(pipeline_id)
    return triggered


def start_scheduler(interval_sec: float = 30, *, stop: threading.Event | None = None) -> threading.Thread:
    stop = stop or threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                tick(datetime.now(timezone.utc))
            except Exception:
                log.exception("管线调度扫描失败")
            stop.wait(interval_sec)

    thread = threading.Thread(target=loop, name="pipeline-scheduler", daemon=True)
    thread.start()
    return thread
