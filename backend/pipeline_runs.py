"""采集管线执行：经现有采集服务运行可配置管线、试跑、记录“本次新增”。

一期运行串行：execute_once 的服务锁与整批采集互斥；锁被占用时抛 CollectorBusyError，
并删除本次写入的 running 运行行，管线的 last_* 恢复原值，调度在下个周期重试。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from backend.adapters import normalize
from backend.collector_jobs import CollectorBusyError
from backend.paths import collector_data_root, collector_source_root
from backend.pipeline_config import PipelineConfigError, default_resolve, next_run_at, to_monitor_config, validate_pipeline
from backend.pipeline_service import PipelineError, get_pipeline
from backend.store import db
from scripts.collect import _collector_environment
from scripts.collector_service import execute_once

RUNNER_DIR = "pipeline-runner"
RUNNER_ENTRYPOINT = "pipeline_runner.py"
STATUS_MAP = {"ok": "success", "partial": "partial", "failed": "failed"}
REPORT_COUNTS = ("records", "candidates", "accepted", "excluded", "errors", "restricted", "duplicates_merged")
ERROR_LIMIT = 1000
SUMMARY_LIMIT = 1000


def _now() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def _base_config_text() -> str:
    return (collector_source_root() / "geopolitical-source-monitor" / "config.json").read_text(encoding="utf-8")


def base_monitor_config() -> dict:
    """仓库内多源采集器的基础配置（只读一次，每次返回新副本）。"""
    return json.loads(_base_config_text())


def collector_id(pipeline_id: str) -> str:
    return f"pipeline-{pipeline_id}"


def registry_item(pipeline: dict, config_path: Path) -> dict:
    return {
        "id": collector_id(pipeline["id"]),
        "name_zh": pipeline["name"],
        "topics": [pipeline["topic"]],
        "entrypoint": RUNNER_ENTRYPOINT,
        "source_dir": RUNNER_DIR,
        "selftest_args": ["--selftest"],
        "output_kind": "geo-v6",
        "enabled": True,
        "env": {"GEO_PIPELINE_CONFIG": str(config_path)},
    }


def _source_types(config: dict) -> dict[str, str]:
    return {s["id"]: s["type"] for s in config.get("sources", [])}


def _parse_time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def record_new_items(conn, *, pipeline_id: str, run_id: str, records: list[dict],
                     source_types: dict[str, str]) -> int:
    """登记本次首次出现的记录；返回新增数。重复调用不会重复登记。"""
    run = conn.execute("SELECT started_at, created_by FROM collection_pipeline_runs WHERE run_id=%s",
                       (run_id,)).fetchone()
    if run is None:
        raise PipelineError("NOT_FOUND", "运行记录不存在", {"run_id": run_id})
    pipeline = conn.execute("SELECT topic FROM collection_pipelines WHERE id=%s", (pipeline_id,)).fetchone()
    topic = pipeline["topic"] if pipeline else None
    started, actor = run["started_at"], run["created_by"]
    fresh: list[dict] = []
    seen_now: set[str] = set()
    for record in records:
        record_id = str(record.get("id") or "")
        if not record_id or record_id in seen_now:
            continue
        seen_now.add(record_id)
        inserted = conn.execute(
            "INSERT INTO pipeline_seen_items(pipeline_id,record_id,first_run_id,created_by,data_timestamp) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (pipeline_id,record_id) DO NOTHING RETURNING record_id",
            (pipeline_id, record_id, run_id, actor, started)).fetchone()
        if inserted:
            fresh.append(record)
    if not fresh:
        return 0
    base = conn.execute("SELECT COALESCE(MAX(seq),0) AS n FROM collection_pipeline_run_items WHERE run_id=%s",
                        (run_id,)).fetchone()["n"]
    floor = datetime.min.replace(tzinfo=timezone.utc)
    fresh.sort(key=lambda r: _parse_time(r.get("published")) or floor, reverse=True)
    monitor = collector_id(pipeline_id)
    for offset, record in enumerate(fresh, start=1):
        published = _parse_time(record.get("published"))
        terms = list(dict.fromkeys([*(record.get("matched_discovery_terms") or []),
                                    *(record.get("matched_admission_terms") or [])]))
        dimension = (record.get("dimensions") or {}).get(topic) if topic else None
        conn.execute(
            "INSERT INTO collection_pipeline_run_items(run_id,seq,pipeline_id,record_id,evidence_id,source_id,source_type,"
            "title,published_at,language,matched_terms,dimension,summary,created_by,data_timestamp) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (run_id, base + offset, pipeline_id, str(record["id"]), normalize(record, monitor)["id"],
             str(record.get("source_id") or ""), source_types.get(record.get("source_id"), "web"),
             str(record.get("title") or "无标题"), published, str(record.get("language") or ""),
             json.dumps(terms, ensure_ascii=False), dimension,
             str(record.get("summary") or "")[:SUMMARY_LIMIT], actor, published or started))
    return len(fresh)


def _new_run_id(pipeline_id: str, started: datetime) -> str:
    return f"{collector_id(pipeline_id)}-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _collector_error(result: dict | None, report: dict) -> str:
    if report.get("error_message"):
        return f"采集服务异常：{report['error_message']}"
    if not result:
        return "采集器没有返回结果"
    detail = (result.get("stderr_tail") or result.get("stdout_tail") or "").strip()
    text = f"采集器失败（退出码 {result.get('returncode')}）"
    return f"{text}：{detail[-500:]}" if detail else text


def _is_busy(exc: BaseException) -> bool:
    return isinstance(exc, CollectorBusyError) or (isinstance(exc, RuntimeError) and "lock is held" in str(exc))


def _start(pipeline: dict, run_id: str, trigger: str, actor: str | None, started: datetime) -> dict:
    with db() as conn:
        previous = conn.execute(
            "SELECT last_run_id,last_status FROM collection_pipelines WHERE id=%s FOR UPDATE", (pipeline["id"],)).fetchone()
        conn.execute(
            "INSERT INTO collection_pipeline_runs(run_id,pipeline_id,version,trigger,status,started_at,created_by,data_timestamp) "
            "VALUES (%s,%s,%s,%s,'running',%s,%s,%s)",
            (run_id, pipeline["id"], pipeline["current_version"], trigger, started, actor, started))
        conn.execute("UPDATE collection_pipelines SET last_run_id=%s,last_status='running' WHERE id=%s",
                     (run_id, pipeline["id"]))
    return dict(previous)


def _discard(pipeline_id: str, run_id: str, previous: dict) -> None:
    with db() as conn:
        conn.execute("DELETE FROM collection_pipeline_runs WHERE run_id=%s AND status='running'", (run_id,))
        conn.execute("UPDATE collection_pipelines SET last_run_id=%s,last_status=%s WHERE id=%s AND last_run_id=%s",
                     (previous["last_run_id"], previous["last_status"], pipeline_id, run_id))


def _finish(pipeline_id: str, run_id: str, *, status: str, stats: dict, snapshot_id: str | None,
            error: str | None, actor: str | None, records: list[dict] | None, source_types: dict[str, str]) -> dict:
    finished = _now()
    with db() as conn:
        new_items = 0
        if records:
            new_items = record_new_items(conn, pipeline_id=pipeline_id, run_id=run_id, records=records,
                                         source_types=source_types)
        stats = {**stats, "new_items": new_items}
        conn.execute(
            "UPDATE collection_pipeline_runs SET status=%s,finished_at=%s,stats=%s,snapshot_id=%s,error=%s,"
            "updated_by=%s,updated_at=%s WHERE run_id=%s",
            (status, finished, json.dumps(stats, ensure_ascii=False), snapshot_id,
             error[:ERROR_LIMIT] if error else None, actor, finished, run_id))
        row = conn.execute("SELECT * FROM collection_pipelines WHERE id=%s FOR UPDATE", (pipeline_id,)).fetchone()
        nxt = next_run_at(row, finished) if row and row["enabled"] else (row["next_run_at"] if row else None)
        # last_* 是运行态字段，不改动 updated_by/updated_at（那两项只反映配置修改）。
        conn.execute(
            "UPDATE collection_pipelines SET last_run_id=%s,last_status=%s,last_finished_at=%s,last_new_items=%s,"
            "next_run_at=%s WHERE id=%s",
            (run_id, status, finished, new_items, nxt, pipeline_id))
        return dict(conn.execute("SELECT * FROM collection_pipeline_runs WHERE run_id=%s", (run_id,)).fetchone())


def run_pipeline(pipeline_id: str, *, trigger: str, actor: str | None,
                 executor: Callable[..., dict] = execute_once) -> dict:
    """同步运行一次可配置管线并返回运行行。锁被占用时抛 CollectorBusyError。"""
    pipeline = get_pipeline(pipeline_id)
    if pipeline["kind"] != "configurable" or not pipeline.get("config"):
        raise PipelineError("INVALID", "只有可配置管线可以由管线执行器运行", {"id": pipeline_id})
    started = _now()
    run_id = _new_run_id(pipeline_id, started)
    previous = _start(pipeline, run_id, trigger, actor, started)
    source_types = _source_types(pipeline["config"])
    item_root = collector_data_root() / collector_id(pipeline_id)
    try:
        monitor = to_monitor_config(pipeline, pipeline["config"], base_monitor_config())
        config_path = item_root / "state" / "pipeline-config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = config_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(monitor, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(config_path)
        item = registry_item(pipeline, config_path)
        report = executor(registry=[item], ids=[item["id"]], run_id_override=run_id,
                          auto_import=False)
    except Exception as exc:
        if _is_busy(exc):
            _discard(pipeline_id, run_id, previous)
            if isinstance(exc, CollectorBusyError):
                raise
            raise CollectorBusyError(str(exc)) from exc
        return _finish(pipeline_id, run_id, status="failed", stats={}, snapshot_id=None,
                       error=f"{type(exc).__name__}: {exc}", actor=actor, records=None, source_types=source_types)

    result = next((r for r in report.get("results") or [] if r.get("id") == item["id"]), None)
    stats: dict[str, Any] = {"import_status": "deferred", "collector_status": report.get("status")}
    if not result or result.get("published") is not True:
        # 未发布时输出目录仍是上一次的结果，不能当作本次数据读取。
        return _finish(pipeline_id, run_id, status="failed", stats=stats, snapshot_id=None,
                       error=_collector_error(result, report), actor=actor, records=None, source_types=source_types)

    output = item_root / "output"
    collection = _read_json(output / "_collection_report.json") or {}
    payload = _read_json(output / "geo-v6.json") or {}
    stats.update({k: collection[k] for k in REPORT_COUNTS if k in collection})
    stats["sources"] = collection.get("sources") or {}
    status = STATUS_MAP.get(collection.get("collection_status") or payload.get("collection_status"), "failed")
    error = None
    if status == "failed":
        error = "采集报告缺失或标记为失败"
    records = [r for r in payload.get("records") or [] if isinstance(r, dict)]
    return _finish(pipeline_id, run_id, status=status, stats=stats, snapshot_id=None,
                   error=error, actor=actor, records=records, source_types=source_types)


def run_builtin(pipeline_id: str, *, trigger: str, actor: str | None,
                executor: Callable[..., dict] = execute_once) -> dict:
    """同步运行一次内置采集器并写运行记录（无逐条“本次新增”）。锁被占用时抛 CollectorBusyError。"""
    pipeline = get_pipeline(pipeline_id)
    if pipeline["kind"] != "builtin":
        raise PipelineError("INVALID", "只有内置管线可以由内置运行入口运行", {"id": pipeline_id})
    started = _now()
    run_id = _new_run_id(pipeline_id, started)
    previous = _start(pipeline, run_id, trigger, actor, started)
    try:
        report = executor(ids=[pipeline_id], auto_import=False, run_id_override=run_id)
    except Exception as exc:
        if _is_busy(exc):
            _discard(pipeline_id, run_id, previous)
            if isinstance(exc, CollectorBusyError):
                raise
            raise CollectorBusyError(str(exc)) from exc
        return _finish(pipeline_id, run_id, status="failed", stats={}, snapshot_id=None,
                       error=f"{type(exc).__name__}: {exc}", actor=actor, records=None, source_types={})
    status = report.get("status") if report.get("status") in ("success", "partial", "failed") else "failed"
    stats = {"import_status": "deferred", "collector_status": report.get("status")}
    error = None
    if status == "failed":
        failed = next((r for r in report.get("results") or [] if r.get("id") == pipeline_id), None)
        error = _collector_error(failed, report)
    return _finish(pipeline_id, run_id, status=status, stats=stats, snapshot_id=None,
                   error=error, actor=actor, records=None, source_types={})


def backfill_merged_runs(snapshot_id: str, *, imported_before: datetime) -> int:
    """整批导入成功后，把导入开始前已完成、尚未归属快照的运行回填为该快照；返回更新行数。"""
    now = _now()
    with db() as conn:
        cur = conn.execute(
            "UPDATE collection_pipeline_runs SET snapshot_id=%s,updated_at=%s WHERE snapshot_id IS NULL "
            "AND status IN ('success','partial') AND finished_at <= %s AND NOT is_deleted",
            (snapshot_id, now, imported_before))
        return cur.rowcount


def _run_process(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def preview_pipeline(row: dict, config: dict, *, limit: int = 5, timeout: int = 120,
                     resolve: Callable[[str], list[str]] = default_resolve) -> dict:
    """试跑：每源列出前 limit 个候选与过滤结果。不写任何表、不写输出目录、不导入。"""
    try:
        clean_row, clean_config = validate_pipeline(row, config, resolve=resolve)
    except PipelineConfigError as exc:
        raise PipelineError("INVALID", exc.message, {"field": exc.field}) from exc
    limit = max(1, min(int(limit), 20))
    monitor = to_monitor_config(clean_row, clean_config, base_monitor_config())
    runner_dir = collector_source_root() / RUNNER_DIR
    with tempfile.TemporaryDirectory(prefix="pipeline-preview-") as tmp:
        config_path = Path(tmp) / "pipeline-config.json"
        config_path.write_text(json.dumps(monitor, ensure_ascii=False), encoding="utf-8")
        base_env = {k: v for k, v in os.environ.items() if not k.startswith("COLLECTOR_")}
        base_env.update({"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        env = _collector_environment({"env": {"GEO_PIPELINE_CONFIG": str(config_path)}}, base_env)
        command = [sys.executable, str(runner_dir / RUNNER_ENTRYPOINT), "--preview", str(limit)]
        try:
            completed = _run_process(command, cwd=runner_dir, env=env, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise PipelineError("PREVIEW_FAILED", f"试跑超时（{timeout} 秒），请减少来源或稍后重试") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-300:]
        raise PipelineError("PREVIEW_FAILED", "试跑失败" + (f"：{detail}" if detail else ""),
                            {"returncode": completed.returncode})
    try:
        payload = json.loads((completed.stdout or "").strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise PipelineError("PREVIEW_FAILED", "试跑输出无法解析") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise PipelineError("PREVIEW_FAILED", "试跑输出格式不正确")
    return {"sources": payload["sources"]}


def list_runs(pipeline_id: str, limit: int = 20) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with db() as conn:
        rows = conn.execute("SELECT * FROM collection_pipeline_runs WHERE pipeline_id=%s AND NOT is_deleted "
                            "ORDER BY started_at DESC, run_id DESC LIMIT %s", (pipeline_id, limit)).fetchall()
        return [dict(r) for r in rows]


def list_run_items(run_id: str, *, offset: int = 0, limit: int = 50) -> list[dict]:
    offset, limit = max(0, int(offset)), max(1, min(int(limit), 500))
    with db() as conn:
        rows = conn.execute("SELECT * FROM collection_pipeline_run_items WHERE run_id=%s AND NOT is_deleted "
                            "ORDER BY seq OFFSET %s LIMIT %s", (run_id, offset, limit)).fetchall()
        return [dict(r) for r in rows]


def recover_interrupted_pipeline_runs(now: datetime) -> list[str]:
    """服务重启后把遗留的 running 运行标记为失败，并为启用的管线重算下次运行时间。"""
    with db() as conn:
        rows = conn.execute("SELECT run_id,pipeline_id FROM collection_pipeline_runs WHERE status='running' "
                            "AND NOT is_deleted ORDER BY started_at FOR UPDATE").fetchall()
        for row in rows:
            conn.execute("UPDATE collection_pipeline_runs SET status='failed',error='SERVICE_INTERRUPTED',finished_at=%s,"
                         "updated_at=%s WHERE run_id=%s", (now, now, row["run_id"]))
            pipeline = conn.execute("SELECT * FROM collection_pipelines WHERE id=%s FOR UPDATE",
                                    (row["pipeline_id"],)).fetchone()
            if pipeline is None:
                continue
            nxt = next_run_at(pipeline, now) if pipeline["enabled"] and not pipeline["is_deleted"] else pipeline["next_run_at"]
            if pipeline["last_run_id"] == row["run_id"]:
                conn.execute("UPDATE collection_pipelines SET last_status='failed',last_finished_at=%s,last_new_items=0,"
                             "next_run_at=%s WHERE id=%s", (now, nxt, pipeline["id"]))
            else:
                conn.execute("UPDATE collection_pipelines SET next_run_at=%s WHERE id=%s", (nxt, pipeline["id"]))
        return [r["run_id"] for r in rows]
