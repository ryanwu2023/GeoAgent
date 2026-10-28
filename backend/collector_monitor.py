"""Read-only projection of collector run reports for the monitoring UI.

This is deliberately a projection, not the scheduler. It lets the UI consume
the run reports that already exist today while the persistent runtime ledger is
implemented in the next phase.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .collector_ledger import CollectorLedger
from .collector_logs import redact


STATUS_MAP = {
    0: ("success", "已完成输出门禁"),
    "timeout": ("failed", "请求超时"),
    "entrypoint_missing": ("failed", "入口缺失"),
}


ERROR_GUIDANCE = {
    "TIMEOUT": ("请求超时", "采集器在限定时间内没有完成，通常是上游响应慢、网络不通或任务卡住。", "先重试一次；若仍超时，检查网络和上游状态，再适当增加单个采集器超时时间。"),
    "CONFIGURATION_REQUIRED": ("缺少运行配置", "缺少采集所需的访问密钥或运行参数，本轮没有生成新观测。", "按日志提示补齐对应环境变量或访问密钥，然后单独补采该采集器。"),
    "UPSTREAM_UNAVAILABLE": ("上游服务不可用", "上游站点返回 5xx 服务错误，当前无法稳定取得数据。", "稍后补采；若连续失败，检查上游公告并启用已配置的备用来源。"),
    "ACCESS_DENIED": ("访问被拒绝", "上游站点拒绝请求，可能需要授权、请求头调整或降低访问频率。", "核对授权与来源访问规则；不要连续高频重试。"),
    "RATE_LIMITED": ("访问频率受限", "上游站点触发限流，本轮请求被暂时拒绝。", "等待限流窗口结束后补采，并降低并发或增加退避时间。"),
    "TLS_CERTIFICATE": ("证书校验失败", "与上游建立安全连接时证书校验失败。", "核对系统时间、证书链和代理设置；不要直接关闭证书校验。"),
    "DNS_FAILURE": ("域名解析失败", "当前环境无法解析上游域名。", "检查 DNS、代理和网络连通性后再补采。"),
    "NETWORK_CONNECTION": ("网络连接失败", "无法连接上游站点或连接被对端中断。", "检查网络、代理和上游可用性，然后单独补采。"),
    "PARSE_ERROR": ("响应解析失败", "上游返回内容与当前解析规则不一致，数据门禁拒绝发布不完整结果。", "查看脱敏日志并核对上游字段变化，修复解析规则后补采。"),
    "DEPENDENCY_MISSING": ("运行依赖缺失", "采集器缺少 Python 模块或本地运行依赖。", "安装项目锁定的依赖后重新运行，不要只在单个采集器目录临时安装。"),
    "ENTRYPOINT_MISSING": ("启动文件缺失", "注册表指向的采集器启动文件不存在。", "核对 collectors.json 的 entrypoint 与项目文件是否一致。"),
    "OUTPUT_NOT_PUBLISHED": ("未生成可发布产物", "进程正常结束，但没有新产物通过输出门禁。", "检查来源返回、输出目录和结构校验结果；确认后再补采。"),
    "SERVICE_INTERRUPTED": ("运行被中断", "项目或采集服务在该采集器完成前停止。", "确认服务已稳定启动，再对该采集器执行补采。"),
    "PROCESS_FAILED": ("采集进程异常", "采集器以非零状态退出，具体原因需结合下方脱敏日志摘要判断。", "查看脱敏日志和来源状态；修复首个明确错误后再补采。"),
}


def _embedded_detail(value: str) -> str | None:
    """Extract a collector-published detail without exposing its full log."""
    stripped = value.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except ValueError:
        return None
    statuses = payload.get("status") if isinstance(payload, dict) else None
    if isinstance(statuses, list):
        for item in statuses:
            if isinstance(item, dict) and item.get("detail"):
                return str(item["detail"])
    return None


def _error_category(item: dict[str, Any], status: str) -> str | None:
    if status == "success" or status == "running":
        return None
    returncode = item.get("returncode")
    text = "\n".join(str(item.get(key) or "") for key in ("stderr_tail", "stdout_tail")).lower()
    if returncode == "timeout":
        return "TIMEOUT"
    if returncode == "entrypoint_missing":
        return "ENTRYPOINT_MISSING"
    if item.get("published") is False and returncode == 0:
        return "OUTPUT_NOT_PUBLISHED"
    if "configuration_required" in text or re.search(r"缺少\s*[a-z0-9_]*api[_-]?key", text, re.I):
        return "CONFIGURATION_REQUIRED"
    if "429" in text or "too many requests" in text or "rate limit" in text:
        return "RATE_LIMITED"
    if re.search(r"(?:http(?: error)?\s*)?(?:500|502|503|504)\b", text):
        return "UPSTREAM_UNAVAILABLE"
    if re.search(r"(?:http(?: error)?\s*)?(?:401|403)\b", text) or "access denied" in text:
        return "ACCESS_DENIED"
    if "certificate_verify_failed" in text or "ssl" in text or "tls" in text:
        return "TLS_CERTIFICATE"
    if "name or service not known" in text or "getaddrinfo" in text or "dns" in text:
        return "DNS_FAILURE"
    if any(term in text for term in ("connection refused", "connection reset", "network is unreachable", "connecterror", "连接失败")):
        return "NETWORK_CONNECTION"
    if any(term in text for term in ("jsondecodeerror", "parse error", "解析失败", "schema", "unexpected field")):
        return "PARSE_ERROR"
    if "modulenotfounderror" in text or "no module named" in text:
        return "DEPENDENCY_MISSING"
    return "PROCESS_FAILED"


def _diagnosis(item: dict[str, Any], status: str) -> dict[str, Any]:
    category = _error_category(item, status)
    if category is None:
        return {"error_type": None, "error_reason": None, "error_detail": None, "suggested_action": None}
    stage, reason, action = ERROR_GUIDANCE[category]
    raw = str(item.get("stderr_tail") or item.get("stdout_tail") or "")
    detail = _embedded_detail(raw) or raw.strip().splitlines()[-1] if raw.strip() else None
    safe_detail = redact(detail)[:500] if detail else None
    return {
        "error_type": category,
        "error_reason": reason,
        "error_detail": safe_detail,
        "suggested_action": action,
        "diagnostic_stage": stage,
    }


def _registry_labels(registry_path: Path) -> dict[str, str]:
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {item["id"]: item.get("name_zh", item["id"]) for item in payload.get("collectors", [])}


def _iso_bounds(results: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    values: list[str] = []
    for item in results:
        for key in ("started_at", "finished_at"):
            value = item.get(key)
            if isinstance(value, str) and value:
                values.append(value)
    return (min(values), max(values)) if values else (None, None)


def _output_count(item: dict[str, Any]) -> int:
    value = item.get("runtime_files")
    if isinstance(value, int):
        return value
    if isinstance(value, (list, dict)):
        return len(value)
    changed = item.get("new_or_changed")
    return len(changed) if isinstance(changed, list) else 0


def _record_count(item: dict[str, Any]) -> int:
    for key in ("record_count", "records_count", "count"):
        value = item.get(key)
        if isinstance(value, int):
            return value
    return 0


def _elapsed_label(item: dict[str, Any]) -> str:
    duration = item.get("duration_sec")
    if isinstance(duration, (int, float)) and duration >= 0:
        return f"{duration:g}秒"
    started = item.get("started_at")
    finished = item.get("finished_at")
    if not isinstance(started, str) or not isinstance(finished, str):
        return "—"
    try:
        seconds = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
    except ValueError:
        return "—"
    return f"{max(0.0, seconds):g}秒"


def _project_item(item: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    collector_id = str(item.get("id", "unknown"))
    returncode = item.get("returncode")
    if item.get("status") == "running":
        status, stage = "running", "采集中"
    elif returncode == 0 and item.get("published") is False:
        status, stage = "partial", "未生成可发布产物"
    else:
        status, stage = STATUS_MAP.get(returncode, ("failed", "采集进程失败"))
    diagnosis = _diagnosis(item, status)
    stage = diagnosis.get("diagnostic_stage") or stage
    attempts = item.get("attempts") if isinstance(item.get("attempts"), int) else 1
    max_attempts = item.get("max_attempts") if isinstance(item.get("max_attempts"), int) else max(1, attempts)
    last_run_at = item.get("finished_at") or item.get("started_at")
    return {
        "id": collector_id,
        "name": labels.get(collector_id, collector_id),
        # The collector registry currently does not carry a business group.
        # Keep that uncertainty visible instead of assigning every runtime
        # collector to the news bucket.
        "group": "未分类",
        "status": status,
        "stage": stage,
        "elapsed": _elapsed_label(item),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at") or "—",
        "attempts": f"{attempts} / {max_attempts}",
        "records": _record_count(item),
        "outputs": _output_count(item),
        "last_run_at": last_run_at,
        "last_success": item.get("finished_at") if status == "success" else None,
        "last_success_at": item.get("finished_at") if status == "success" else None,
        "source_health": "运行报告已完成" if status == "success" else "运行报告未完成",
        **{key: value for key, value in diagnosis.items() if key != "diagnostic_stage"},
        # Keep the legacy field for older frontends, but make it a concise,
        # redacted reason rather than a raw stdout/stderr dump.
        "error_message": diagnosis.get("error_reason"),
    }


def _load_report(report: Path, labels: dict[str, str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw_results = payload.get("results", []) if isinstance(payload, dict) else payload
    if not isinstance(raw_results, list):
        return None
    results = [_project_item(item, labels) for item in raw_results if isinstance(item, dict)]
    started_at, finished_at = _iso_bounds(raw_results)
    if isinstance(payload, dict):
        started_at = started_at or payload.get("started_at")
        finished_at = finished_at or payload.get("finished_at")
    failed = sum(item["status"] == "failed" for item in results)
    return {
        "available": True,
        "source": "collection-run-report",
        "run_id": report.parent.name,
        "report_path": str(report),
        "run_kind": payload.get("run_kind") if isinstance(payload, dict) else "collector-run",
        "run_status": payload.get("status") if isinstance(payload, dict) else None,
        "import_status": payload.get("import_status") if isinstance(payload, dict) else None,
        "snapshot_id": payload.get("snapshot_id") if isinstance(payload, dict) else None,
        "import_result": payload.get("import_result") if isinstance(payload, dict) else None,
        "import_error": payload.get("import_error_message") if isinstance(payload, dict) else None,
        # A running report has no finished_at yet.  Expose its start time so
        # the monitor can show the active batch instead of falling back to an
        # older completed run.
        "updated_at": finished_at or started_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "collector_count": len(results),
        "failed_count": failed,
        "collectors": results,
    }


def load_runs(runs_root: Path, registry_path: Path, limit: int = 20, *, run_id: str = '') -> list[dict[str, Any]]:
    """Return parseable persisted reports newest first."""
    reports = []
    if runs_root.is_dir():
        # state.json is written before collectors start. Including it makes a
        # currently running manual/scheduled batch visible instead of leaving
        # the UI on the previous completed report until results.json appears.
        report_dirs = {
            path.parent
            for path in runs_root.glob("*/results.json")
            if path.is_file()
        }
        report_dirs.update(
            path.parent
            for path in runs_root.glob("*/state.json")
            if path.is_file()
        )
        reports = list(
            directory / ("results.json" if (directory / "results.json").is_file() else "state.json")
            for directory in report_dirs
            if not run_id or directory.name == run_id
        )

        def sort_key(path: Path) -> tuple[str, float]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                started_at = payload.get("started_at") if isinstance(payload, dict) else None
            except (OSError, ValueError):
                started_at = None
            # Sort by batch start time. File mtime changes on every live
            # progress write and must not move an older batch above a newer one.
            return (started_at if isinstance(started_at, str) else "", path.stat().st_mtime)

        reports.sort(key=sort_key, reverse=True)
    labels = _registry_labels(registry_path)
    ledger = _ledger(runs_root)
    runs: list[dict[str, Any]] = []
    # Parse the full persisted history before applying the response limit. A
    # failed current run needs an older successful run to populate
    # ``last_success_at``; slicing first made every failure look as if it had
    # never succeeded.
    for report in reports:
        parsed = _load_report(report, labels)
        if parsed is not None:
            # The compatibility state.json can remain `running` when the
            # service is interrupted after collectors have finished. The
            # ledger is authoritative for real lifecycle states in that case.
            # `ensure_run()` also creates a `historical` placeholder for old
            # reports so reviews can be stored; that placeholder must not
            # overwrite the report's actual success/partial/failed status.
            ledger_run = ledger.run(parsed["run_id"]) if ledger is not None else None
            lifecycle_statuses = {"running", "success", "partial", "failed", "abandoned"}
            if (
                ledger_run is not None
                and ledger_run.get("status") in lifecycle_statuses
                and ledger_run.get("status") != parsed.get("run_status")
            ):
                parsed["run_status"] = ledger_run.get("status")
                parsed["finished_at"] = ledger_run.get("finished_at") or parsed.get("finished_at")
                parsed["updated_at"] = parsed.get("finished_at") or parsed.get("updated_at")
                parsed["import_status"] = ledger_run.get("import_status") or parsed.get("import_status")
                parsed["snapshot_id"] = ledger_run.get("snapshot_id") or parsed.get("snapshot_id")
                if ledger_run.get("status") == "abandoned":
                    for collector in parsed.get("collectors", []):
                        if collector.get("status") == "running":
                            collector.update(
                                status="failed",
                                stage="运行中断",
                                source_health="服务进程已中断",
                                error_type="SERVICE_INTERRUPTED",
                                error_reason=ERROR_GUIDANCE["SERVICE_INTERRUPTED"][1],
                                error_message=ERROR_GUIDANCE["SERVICE_INTERRUPTED"][1],
                                error_detail="项目或采集服务在该采集器完成前停止",
                                suggested_action=ERROR_GUIDANCE["SERVICE_INTERRUPTED"][2],
                            )
                    parsed["failed_count"] = sum(item.get("status") == "failed" for item in parsed.get("collectors", []))
            runs.append(parsed)
    last_success_by_collector: dict[str, str] = {}
    for run in reversed(runs):
        for collector in run.get("collectors", []):
            collector_id = str(collector.get("id", ""))
            current = collector.get("finished_at") or collector.get("started_at")
            collector["last_run_at"] = collector.get("last_run_at") or current
            if collector.get("status") == "success" and isinstance(current, str) and current:
                last_success_by_collector[collector_id] = current
            previous_success = last_success_by_collector.get(collector_id)
            collector["last_success_at"] = previous_success
            # Legacy field remains available for existing clients.
            collector["last_success"] = previous_success
    return runs[: max(0, limit)]


def load_latest_run(runs_root: Path, registry_path: Path) -> dict[str, Any]:
    """Return the newest parseable report, or an explicit unavailable state."""
    runs = load_runs(runs_root, registry_path, limit=1)
    if runs:
        return runs[0]
    return {
        "available": False,
        "source": "none",
        "run_id": None,
        "updated_at": None,
        "collector_count": 0,
        "failed_count": 0,
        "collectors": [],
    }


def _ledger(runs_root: Path) -> CollectorLedger | None:
    try:
        return CollectorLedger()
    except (OSError, ValueError):
        return None


def load_ledger_runs(runs_root: Path, limit: int = 20) -> dict[str, Any]:
    """Expose the scheduler ledger without replacing report history."""
    ledger = _ledger(runs_root)
    if ledger is None:
        return {"available": False, "source": "none", "runs": []}
    return {"available": True, "source": "collector-ledger", "runs": ledger.runs(limit)}


def load_ledger_events(
    runs_root: Path,
    run_id: str,
    *,
    collector_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return event records for a run, optionally narrowed to one collector."""
    ledger = _ledger(runs_root)
    if ledger is None or ledger.run(run_id) is None:
        return {"available": False, "source": "none", "run_id": run_id, "events": []}
    events = ledger.events(run_id, limit=max(0, limit))
    if collector_id:
        events = [
            event for event in events
            if event.get("collector_id") in {None, collector_id}
        ]
    return {
        "available": True,
        "source": "collector-ledger",
        "run_id": run_id,
        "collector_id": collector_id,
        "events": events,
    }


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _source_summary(sources: list[dict[str, Any]]) -> dict[str, int]:
    healthy = sum(item.get("state") == "ok" for item in sources)
    stale = sum("out-of-window" in str(item.get("state", "")) or item.get("state") == "stale" for item in sources)
    disabled = sum(item.get("state") == "disabled" or item.get("enabled") is False for item in sources)
    failed = len(sources) - healthy - stale - disabled
    entries = sum(item.get("entries", 0) for item in sources if isinstance(item.get("entries"), int))
    kept = sum(item.get("kept", 0) for item in sources if isinstance(item.get("kept"), int))
    return {
        "total": len(sources),
        "healthy": healthy,
        "stale": stale,
        "failed": max(0, failed),
        "disabled": disabled,
        "entries": entries,
        "kept": kept,
    }


def _project_source_status(output: Path) -> dict[str, Any] | None:
    artifact_payload = _read_json(output / "_source_artifact.json") or {}
    status_payload = _read_json(output / "_source_status.json")
    fetch_payload = _read_json(output / "_fetch_stats.json")
    health_payload = _read_json(output / "_source_health.json") or {}
    if status_payload is None and fetch_payload is None and not health_payload and not artifact_payload:
        return None

    source_map: dict[str, dict[str, Any]] = {}
    if status_payload:
        for raw in status_payload.get("sources", []):
            if isinstance(raw, dict) and raw.get("id"):
                source_map[str(raw["id"])] = dict(raw)
    if fetch_payload:
        for source_id, raw in (fetch_payload.get("src") or {}).items():
            if not isinstance(raw, dict):
                continue
            item = source_map.setdefault(str(source_id), {"id": str(source_id)})
            item.setdefault("state", "ok" if raw.get("ok") else "failed")
            if raw.get("ok") is False:
                item["state"] = "failed"
            if isinstance(raw.get("status"), int):
                item["http_status"] = raw["status"]
            if isinstance(raw.get("n"), int):
                item["entries"] = raw["n"]
            if raw.get("err"):
                item["error"] = str(raw["err"])[:500]
    for source_id, raw in health_payload.items():
        if not isinstance(raw, dict):
            continue
        item = source_map.setdefault(str(source_id), {"id": str(source_id)})
        failures = raw.get("consec_fail")
        if isinstance(failures, int):
            item["consecutive_failures"] = failures
            if failures > 0 and item.get("state") == "ok":
                item["state"] = "degraded"
        if raw.get("last_ok"):
            item["last_ok"] = raw["last_ok"]

    sources = sorted(source_map.values(), key=lambda item: str(item.get("id", "")))
    updated_at = (status_payload or {}).get("run_at") or (fetch_payload or {}).get("run")
    return {
        "updated_at": updated_at,
        "artifact_run_id": artifact_payload.get("run_id"),
        "artifact_status": artifact_payload.get("status", "available"),
        "published_this_run": artifact_payload.get("published_this_run"),
        "artifact_files": artifact_payload.get("files", []),
        "proxy": (status_payload or {}).get("proxy"),
        "source_status_file": status_payload is not None,
        "fetch_stats_file": fetch_payload is not None,
        "health_file": bool(health_payload),
        "summary": _source_summary(sources),
        "sources": sources,
    }


def load_source_status(
    data_root: Path,
    collector_id: str | None = None,
    *,
    collector_ids: set[str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Project source health, optionally narrowed to collectors in one run.

    Without a run id this is the latest published output.  With a run id it
    reads only the immutable source-artifact archive captured for that run;
    missing archives stay unavailable instead of being replaced by newer data.
    """
    run_artifact_root = data_root.parent / "collection-runs" / run_id if run_id else None
    if run_id:
        # A historical run must use its immutable run directory.  Falling
        # back to the current published tree would make old reports appear to
        # have the latest source health.
        artifact_base = run_artifact_root if run_artifact_root and run_artifact_root.is_dir() else None
        if artifact_base is None:
            outputs = []
        elif collector_id:
            outputs = [artifact_base / collector_id / "source-status"]
        elif collector_ids is not None:
            outputs = [artifact_base / item / "source-status" for item in sorted(collector_ids)]
        else:
            outputs = sorted(path / "source-status" for path in artifact_base.iterdir() if path.is_dir())
    elif collector_id:
        outputs = [data_root / collector_id / "output"]
    elif collector_ids is not None:
        outputs = [data_root / item / "output" for item in sorted(collector_ids)]
    elif data_root.is_dir():
        outputs = sorted(path / "output" for path in data_root.iterdir() if path.is_dir())
    else:
        outputs = []
    collectors: list[dict[str, Any]] = []
    for output in outputs:
        if not output.is_dir():
            continue
        if collector_ids is not None and output.parent.name not in collector_ids:
            continue
        projected = _project_source_status(output)
        if projected is not None:
            collectors.append({"collector_id": output.parent.name, **projected})
    result = {
        "available": bool(collectors),
        "source": "collector-run-source-status" if run_id and collectors else "collector-output-source-status" if collectors else "none",
        "collector_id": collector_id,
        "collectors": collectors,
    }
    if run_id is not None:
        result["run_id"] = run_id
        result["scope"] = "run-collector-source-artifact"
    return result
