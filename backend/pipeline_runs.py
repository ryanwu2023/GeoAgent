"""采集管线执行：随整批运行的管线、隔离的测试运行、试跑、记录“本次新增”。

管线不单独定时（2026-11-11 用户决定）：
- 整批运行：execute_once 整批（未指定 ids）时把已启用的配置化管线（batch_pipeline_items）追加到当次
  注册表，产物发布到正式目录随整批导入；整批结束后 record_batch_pipeline_runs 写运行记录
  （trigger=batch、snapshot_id=本次快照）与本次新增，并写去重台账。
- 测试运行：run_trial 在 collector_data_root()/.pipeline-trials/ 下用 run_one 运行，只占该管线自己的
  单采集器锁，不经过整批服务锁与整批台账；产物不被导入扫描到，永不进快照；本次新增对照去重台账
  计算但不写台账。
- 停用/删除的配置化管线，其正式目录 pipeline-<id>（含 output 与 state 增量状态，如 HTTP 缓存）整体移到
  .pipeline-retired/；重新启用后从零抓取。整批导入前 sweep_stale_pipeline_outputs 再清扫一次，
  兜住整批进行中被停用、退役后又被本批发布的目录。
"""
from __future__ import annotations

import re

import json
import logging
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
from backend.paths import collector_data_root, collector_source_root
from backend.pipeline_config import PipelineConfigError, default_resolve, to_monitor_config, validate_pipeline
from backend.pipeline_scheduler import pipelines_enabled
from backend.pipeline_service import PipelineError, get_pipeline, retire_pipeline_output
from backend.store import db
from scripts.collect import _collector_environment, run_one

log = logging.getLogger(__name__)

RUNNER_DIR = "pipeline-runner"
PIPELINE_PREFIX = "pipeline-"
RUNNER_ENTRYPOINT = "pipeline_runner.py"
STATUS_MAP = {"ok": "success", "partial": "partial", "failed": "failed"}
REPORT_COUNTS = ("records", "candidates", "accepted", "excluded", "errors", "restricted", "duplicates_merged")
ERROR_LIMIT = 1000
SUMMARY_LIMIT = 1000
TRIAL_DIR = ".pipeline-trials"
TRIAL_TIMEOUT_SEC = 900
BATCH_ACTOR = "system"
BUILTIN_NO_TRIAL = "内置采集器随整批运行，不支持测试运行"
TRIAL_CONFLICT = "该管线正在测试运行"
UNPUBLISHED = "采集产物未通过发布门禁"
RUN_META = "pipeline-run.json"
PIPELINE_DIR_RE = re.compile(r"^pipeline-(p-[0-9a-f]+)$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def _base_config_text() -> str:
    return (collector_source_root() / "geopolitical-source-monitor" / "config.json").read_text(encoding="utf-8")


def base_monitor_config() -> dict:
    """仓库内多源采集器的基础配置（只读一次，每次返回新副本）。"""
    return json.loads(_base_config_text())


def collector_id(pipeline_id: str) -> str:
    return f"{PIPELINE_PREFIX}{pipeline_id}"


def registry_item(pipeline: dict, config_path: Path) -> dict:
    return {
        "id": collector_id(pipeline["id"]),
        "name_zh": pipeline["name"],
        "topics": list(pipeline["topics"]),
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
                     source_types: dict[str, str], write_ledger: bool = True) -> int:
    """登记本次首次出现的记录；返回新增数。重复调用不会重复登记。

    write_ledger=False（测试运行）：只对照去重台账判断新增并写本次新增条目，不写台账。
    """
    run = conn.execute("SELECT started_at, created_by FROM collection_pipeline_runs WHERE run_id=%s",
                       (run_id,)).fetchone()
    if run is None:
        raise PipelineError("NOT_FOUND", "运行记录不存在", {"run_id": run_id})
    pipeline = conn.execute("SELECT topics FROM collection_pipelines WHERE id=%s", (pipeline_id,)).fetchone()
    topics = list(pipeline["topics"]) if pipeline else []
    started, actor = run["started_at"], run["created_by"]
    fresh: list[dict] = []
    seen_now: set[str] = set()
    for record in records:
        record_id = str(record.get("id") or "")
        if not record_id or record_id in seen_now:
            continue
        seen_now.add(record_id)
        if write_ledger:
            is_new = conn.execute(
                "INSERT INTO pipeline_seen_items(pipeline_id,record_id,first_run_id,created_by,data_timestamp) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (pipeline_id,record_id) DO NOTHING RETURNING record_id",
                (pipeline_id, record_id, run_id, actor, started)).fetchone() is not None
        else:
            is_new = conn.execute("SELECT 1 FROM pipeline_seen_items WHERE pipeline_id=%s AND record_id=%s",
                                  (pipeline_id, record_id)).fetchone() is None
        if is_new:
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
        raw_dims = record.get("dimensions") or {}
        dimensions = {t: raw_dims[t] for t in topics if raw_dims.get(t)}
        conn.execute(
            "INSERT INTO collection_pipeline_run_items(run_id,seq,pipeline_id,record_id,evidence_id,source_id,source_type,"
            "title,published_at,language,matched_terms,dimensions,summary,created_by,data_timestamp) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (run_id, base + offset, pipeline_id, str(record["id"]), normalize(record, monitor)["id"],
             str(record.get("source_id") or ""), source_types.get(record.get("source_id"), "web"),
             str(record.get("title") or "无标题"), published, str(record.get("language") or ""),
             json.dumps(terms, ensure_ascii=False), json.dumps(dimensions, ensure_ascii=False),
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


def _collector_error(result: dict | None, report: dict | None = None) -> str:
    if report and report.get("error_message"):
        return f"采集服务异常：{report['error_message']}"
    if not result:
        return "采集器没有返回结果"
    detail = (result.get("stderr_tail") or result.get("stdout_tail") or "").strip()
    text = f"采集器失败（退出码 {result.get('returncode')}）"
    return f"{text}：{detail[-500:]}" if detail else text


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".json.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_monitor_config(pipeline: dict, root: Path) -> Path:
    """把管线配置翻译成多源采集器配置，原子写到 <root>/pipeline-<id>/state/pipeline-config.json。"""
    monitor = to_monitor_config(pipeline, pipeline["config"], base_monitor_config())
    config_path = root / collector_id(pipeline["id"]) / "state" / "pipeline-config.json"
    _atomic_write_json(config_path, monitor)
    return config_path


def _outcome(result: dict | None, output: Path, stats: dict) -> tuple[str, dict, str | None, list[dict] | None]:
    """按单采集器结果与已发布产物给出 (状态, 统计, 错误, 记录)。未发布时不读产物（目录里是上一次的结果）。"""
    stats = dict(stats)
    if not result or result.get("published") is not True:
        if result and result.get("returncode") == 0:
            return "failed", stats, UNPUBLISHED, None
        return "failed", stats, _collector_error(result), None
    collection = _read_json(output / "_collection_report.json") or {}
    payload = _read_json(output / "geo-v6.json") or {}
    stats.update({k: collection[k] for k in REPORT_COUNTS if k in collection})
    stats["sources"] = collection.get("sources") or {}
    status = STATUS_MAP.get(collection.get("collection_status") or payload.get("collection_status"), "failed")
    error = "采集报告缺失或标记为失败" if status == "failed" else None
    records = [r for r in payload.get("records") or [] if isinstance(r, dict)]
    return status, stats, error, records


def _complete(conn, pipeline_id: str, run_id: str, *, status: str, stats: dict, snapshot_id: str | None,
              error: str | None, actor: str | None, records: list[dict] | None, source_types: dict[str, str],
              write_ledger: bool, finished: datetime, trigger: str) -> dict:
    new_items = 0
    if records:
        new_items = record_new_items(conn, pipeline_id=pipeline_id, run_id=run_id, records=records,
                                     source_types=source_types, write_ledger=write_ledger)
    stats = {**stats, "new_items": new_items}
    conn.execute(
        "UPDATE collection_pipeline_runs SET status=%s,finished_at=%s,stats=%s,snapshot_id=%s,error=%s,"
        "updated_by=%s,updated_at=%s WHERE run_id=%s",
        (status, finished, json.dumps(stats, ensure_ascii=False), snapshot_id,
         error[:ERROR_LIMIT] if error else None, actor, finished, run_id))
    # last_* 是运行态字段，不改动 updated_by/updated_at（那两项只反映配置修改）。
    conn.execute(
        "UPDATE collection_pipelines SET last_run_id=%s,last_trigger=%s,last_status=%s,last_finished_at=%s,"
        "last_new_items=%s WHERE id=%s",
        (run_id, trigger, status, finished, new_items, pipeline_id))
    return dict(conn.execute("SELECT * FROM collection_pipeline_runs WHERE run_id=%s", (run_id,)).fetchone())


# —— 随整批运行 ——

def batch_pipeline_items() -> list[dict]:
    """整批注册表要追加的已启用配置化管线；开关关闭或出错时返回 [] 并告警。"""
    if not pipelines_enabled():
        return []
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT p.*, v.config FROM collection_pipelines p JOIN collection_pipeline_versions v "
                "ON v.pipeline_id=p.id AND v.version=p.current_version AND NOT v.is_deleted "
                "WHERE p.kind='configurable' AND p.enabled AND NOT p.is_deleted ORDER BY p.id").fetchall()
    except Exception as exc:  # noqa: BLE001 - 数据库不可用时整批照常运行内置采集器
        log.warning("读取已启用管线失败，本次整批不含配置化管线：%s", exc)
        return []
    root = collector_data_root()
    items = []
    for row in rows:
        pipeline = dict(row)
        try:
            config_path = _write_monitor_config(pipeline, root)
            # 记下本次实际运行的版本与来源类型，整批结束写运行记录时据此归属（期间配置可能被修改）。
            _atomic_write_json(config_path.parent / RUN_META, {
                "version": pipeline["current_version"], "source_types": _source_types(pipeline["config"])})
            items.append(registry_item(pipeline, config_path))
        except Exception as exc:  # noqa: BLE001 - 单条管线配置坏了不影响其它管线
            log.warning("管线 %s 配置无法翻译，本次整批跳过：%s", pipeline["id"], exc)
    return items


def _enabled_configurable_ids() -> set[str]:
    with db() as conn:
        rows = conn.execute("SELECT id FROM collection_pipelines WHERE kind='configurable' AND enabled "
                            "AND NOT is_deleted").fetchall()
    return {r["id"] for r in rows}


def sweep_stale_pipeline_outputs() -> list[str]:
    """导入前退役不属于“已启用、未删除的配置化管线”的 pipeline-p-<hex> 目录；返回退役的管线 id。

    查询失败只告警、不清扫（宁可多导入一次，也不误删正常管线的产物）。
    """
    root = collector_data_root()
    try:
        candidates = sorted(m.group(1) for d in root.iterdir() if d.is_dir() and (m := PIPELINE_DIR_RE.match(d.name)))
    except FileNotFoundError:
        return []
    if not candidates:
        return []
    try:
        active = _enabled_configurable_ids()
    except Exception as exc:  # noqa: BLE001
        log.warning("查询已启用管线失败，本次导入前不清扫管线产物：%s", exc)
        return []
    retired = [pid for pid in candidates if pid not in active and retire_pipeline_output(pid) is not None]
    if retired:
        log.info("导入前退役了未启用管线的产物：%s", ", ".join(retired))
    return retired


def _report_time(value: Any, fallback: datetime) -> datetime:
    return _parse_time(value) or fallback


def _run_meta(conn, path: Path, pipeline_id: str) -> dict:
    """整批开始时写下的版本与来源类型；文件缺失（如期间被停用退役）时回退到当前版本。"""
    meta = _read_json(path)
    if meta and isinstance(meta.get("version"), int) and isinstance(meta.get("source_types"), dict):
        return meta
    row = conn.execute(
        "SELECT p.current_version,v.config FROM collection_pipelines p LEFT JOIN collection_pipeline_versions v "
        "ON v.pipeline_id=p.id AND v.version=p.current_version AND NOT v.is_deleted WHERE p.id=%s",
        (pipeline_id,)).fetchone()
    return {"version": row["current_version"], "source_types": _source_types(row["config"] or {})}


def record_batch_pipeline_runs(report: dict, snapshot_id: str | None) -> list[str]:
    """整批结束后为每条参与的管线写运行记录、本次新增与去重台账；返回写入的 run_id。

    run_id = <整批 run_id>:<管线 id>，同一整批重复调用不会重复写。单条失败只告警。
    """
    batch_run_id = str(report.get("run_id") or "")
    now = _now()
    batch_started = _report_time(report.get("started_at"), now)
    batch_finished = _report_time(report.get("finished_at"), now)
    root = collector_data_root()
    written: list[str] = []
    for result in report.get("results") or []:
        cid = str(result.get("id") or "")
        if not cid.startswith(PIPELINE_PREFIX) or not batch_run_id:
            continue
        pipeline_id = cid[len(PIPELINE_PREFIX):]
        run_id = f"{batch_run_id}:{pipeline_id}"
        try:
            with db() as conn:
                pipeline = conn.execute("SELECT id,kind FROM collection_pipelines WHERE id=%s FOR UPDATE",
                                        (pipeline_id,)).fetchone()
                if not pipeline or pipeline["kind"] != "configurable":
                    continue
                meta = _run_meta(conn, root / cid / "state" / RUN_META, pipeline_id)
                if conn.execute("SELECT 1 FROM collection_pipeline_runs WHERE run_id=%s", (run_id,)).fetchone():
                    continue
                started = _report_time(result.get("started_at"), batch_started)
                finished = _report_time(result.get("finished_at"), batch_finished)
                stats = {"import_status": report.get("import_status"), "collector_status": report.get("status")}
                status, stats, error, records = _outcome(result, root / cid / "output", stats)
                conn.execute(
                    "INSERT INTO collection_pipeline_runs(run_id,pipeline_id,version,trigger,status,started_at,"
                    "created_by,data_timestamp) VALUES (%s,%s,%s,'batch',%s,%s,%s,%s)",
                    (run_id, pipeline_id, meta["version"], status, started, BATCH_ACTOR, started))
                # 本次没有产物发布（失败或未过门禁）时，快照里没有它这次的数据，不挂快照。
                _complete(conn, pipeline_id, run_id, status=status, stats=stats,
                          snapshot_id=snapshot_id if result.get("published") is True else None,
                          error=error, actor=BATCH_ACTOR, records=records,
                          source_types=meta["source_types"], write_ledger=True,
                          finished=max(finished, started), trigger="batch")
            written.append(run_id)
        except Exception as exc:  # noqa: BLE001 - 不影响整批结果与其它管线
            log.warning("写入管线 %s 的整批运行记录失败：%s", pipeline_id, exc)
    return written


# —— 测试运行 ——

def trial_root() -> Path:
    return collector_data_root() / TRIAL_DIR


def trial_running(pipeline_id: str) -> bool:
    with db() as conn:
        return conn.execute(
            "SELECT 1 FROM collection_pipeline_runs WHERE pipeline_id=%s AND trigger='trial' AND status='running' "
            "AND NOT is_deleted LIMIT 1", (pipeline_id,)).fetchone() is not None


def _start_trial(pipeline: dict, run_id: str, actor: str | None, started: datetime) -> dict:
    with db() as conn:
        previous = conn.execute(
            "SELECT last_run_id,last_trigger,last_status FROM collection_pipelines WHERE id=%s FOR UPDATE",
            (pipeline["id"],)).fetchone()
        if conn.execute("SELECT 1 FROM collection_pipeline_runs WHERE pipeline_id=%s AND trigger='trial' "
                        "AND status='running' AND NOT is_deleted LIMIT 1", (pipeline["id"],)).fetchone():
            raise PipelineError("CONFLICT", TRIAL_CONFLICT, {"id": pipeline["id"]})
        conn.execute(
            "INSERT INTO collection_pipeline_runs(run_id,pipeline_id,version,trigger,status,started_at,created_by,data_timestamp) "
            "VALUES (%s,%s,%s,'trial','running',%s,%s,%s)",
            (run_id, pipeline["id"], pipeline["current_version"], started, actor, started))
        conn.execute("UPDATE collection_pipelines SET last_run_id=%s,last_trigger='trial',last_status='running' WHERE id=%s",
                     (run_id, pipeline["id"]))
    return dict(previous)


def _discard(pipeline_id: str, run_id: str, previous: dict) -> None:
    with db() as conn:
        conn.execute("DELETE FROM collection_pipeline_runs WHERE run_id=%s AND status='running'", (run_id,))
        conn.execute("UPDATE collection_pipelines SET last_run_id=%s,last_trigger=%s,last_status=%s "
                     "WHERE id=%s AND last_run_id=%s",
                     (previous["last_run_id"], previous["last_trigger"], previous["last_status"], pipeline_id, run_id))


def _finish_trial(pipeline_id: str, run_id: str, *, status: str, stats: dict, error: str | None,
                  actor: str | None, records: list[dict] | None, source_types: dict[str, str]) -> dict:
    with db() as conn:
        return _complete(conn, pipeline_id, run_id, status=status, stats=stats, snapshot_id=None, error=error,
                         actor=actor, records=records, source_types=source_types, write_ledger=False,
                         finished=_now(), trigger="trial")


def run_trial(pipeline_id: str, *, actor: str | None, runner: Callable[..., dict] = run_one,
              timeout: int = TRIAL_TIMEOUT_SEC) -> dict:
    """同步测试运行一次配置化管线并返回运行行；不进快照、不写去重台账、不占整批锁。

    同一管线已有测试运行进行中时抛 PipelineError("CONFLICT")。
    """
    pipeline = get_pipeline(pipeline_id)
    if pipeline["kind"] != "configurable" or not pipeline.get("config"):
        raise PipelineError("INVALID", BUILTIN_NO_TRIAL, {"id": pipeline_id})
    started = _now()
    run_id = _new_run_id(pipeline_id, started)
    previous = _start_trial(pipeline, run_id, actor, started)
    source_types = _source_types(pipeline["config"])
    root = trial_root()
    stats: dict[str, Any] = {"import_status": "not_imported"}
    try:
        item = registry_item(pipeline, _write_monitor_config(pipeline, root))
        result = runner(item, timeout=timeout, data_root=root)
    except Exception as exc:
        if isinstance(exc, RuntimeError) and "lock is held" in str(exc):
            # 另一进程正在测试运行同一管线（单采集器锁被占用）。
            _discard(pipeline_id, run_id, previous)
            raise PipelineError("CONFLICT", TRIAL_CONFLICT, {"id": pipeline_id}) from exc
        return _finish_trial(pipeline_id, run_id, status="failed", stats=stats,
                             error=f"{type(exc).__name__}: {exc}", actor=actor, records=None, source_types=source_types)
    status, stats, error, records = _outcome(result, root / item["id"] / "output", stats)
    return _finish_trial(pipeline_id, run_id, status=status, stats=stats, error=error, actor=actor,
                         records=records, source_types=source_types)


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
    """服务重启后把遗留的 running 运行标记为失败（SERVICE_INTERRUPTED）。"""
    with db() as conn:
        rows = conn.execute("SELECT run_id,pipeline_id,trigger FROM collection_pipeline_runs WHERE status='running' "
                            "AND NOT is_deleted ORDER BY started_at FOR UPDATE").fetchall()
        for row in rows:
            conn.execute("UPDATE collection_pipeline_runs SET status='failed',error='SERVICE_INTERRUPTED',finished_at=%s,"
                         "updated_at=%s WHERE run_id=%s", (now, now, row["run_id"]))
            conn.execute("UPDATE collection_pipelines SET last_status='failed',last_trigger=%s,last_finished_at=%s,"
                         "last_new_items=0 WHERE id=%s AND last_run_id=%s",
                         (row["trigger"], now, row["pipeline_id"], row["run_id"]))
        return [r["run_id"] for r in rows]
