"""Read-only projection of collector run reports for the monitoring UI.

This is deliberately a projection, not the scheduler. It lets the UI consume
the run reports that already exist today while the persistent runtime ledger is
implemented in the next phase.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .collector_ledger import CollectorLedger


STATUS_MAP = {
    0: ("success", "已完成输出门禁"),
    "timeout": ("failed", "请求超时"),
    "entrypoint_missing": ("failed", "入口缺失"),
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
    status, stage = ("running", "采集中") if item.get("status") == "running" else STATUS_MAP.get(returncode, ("failed", "采集进程失败"))
    error_type = None
    if returncode == "timeout":
        error_type = "TIMEOUT"
    elif returncode not in (0, None):
        error_type = "COLLECTOR_PROCESS"
    error_message = (item.get("stderr_tail") or item.get("stdout_tail") or None) if status != "success" else None
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
        "attempts": "1 / 2",
        "records": _record_count(item),
        "outputs": _output_count(item),
        "last_success": item.get("finished_at") if status == "success" else None,
        "source_health": "运行报告已完成" if status == "success" else "运行报告未完成",
        "error_type": error_type,
        "error_message": error_message[-1000:] if isinstance(error_message, str) else None,
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


def load_runs(runs_root: Path, registry_path: Path, limit: int = 20) -> list[dict[str, Any]]:
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
    runs: list[dict[str, Any]] = []
    for report in reports[: max(0, limit)]:
        parsed = _load_report(report, labels)
        if parsed is not None:
            runs.append(parsed)
    return runs


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
    path = runs_root / "ledger.sqlite3"
    if not path.is_file():
        return None
    try:
        return CollectorLedger(path)
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
