"""Small, platform-neutral collector service entry point.

The operating system is responsible for starting/restarting this process. The
service owns the interval and report lifecycle, while ``scripts.collect``
remains the compatibility runner for individual collectors.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.paths import project_root
from backend.paths import collector_data_root
from backend.collector_ledger import CollectorLedger, service_lock
from backend.collector_import import import_collector_output
from scripts.collect import run_collectors
from scripts.integrate_collectors import load_registry


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_id(now: datetime | None = None) -> str:
    return (now or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _archive_source_artifacts(report_dir: Path, results: list[dict], data_root: Path) -> None:
    """Copy source-health evidence into the immutable run directory.

    The published tree is intentionally still the serving location, but a run
    must retain the exact source-status files that were visible when it
    finished.  A missing artifact is recorded explicitly instead of silently
    falling back to a later or older run.
    """
    source_files = ("_source_status.json", "_fetch_stats.json", "_source_health.json")
    for item in results:
        collector_id = str(item.get("id", "unknown"))
        live_output = data_root / collector_id / "output"
        artifact_dir = report_dir / collector_id / "source-status"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for filename in source_files:
            source = live_output / filename
            if source.is_file():
                shutil.copy2(source, artifact_dir / filename)
                copied.append(filename)
        _write_json(
            artifact_dir / "_source_artifact.json",
            {
                "run_id": report_dir.name,
                "collector_id": collector_id,
                "captured_at": utc_now().isoformat(),
                "published_this_run": item.get("published") is True,
                "status": "available" if copied else "missing",
                "files": copied,
            },
        )


def _result_status(item: dict) -> str:
    return "success" if item.get("returncode") in (0, "not_provided") else "failed"


def _result_message(item: dict) -> str | None:
    for key in ("stderr_tail", "stdout_tail"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value[-2000:]
    return None


def execute_once(
    *,
    ids: list[str] | None = None,
    exclude_ids: list[str] | None = None,
    topic: str | None = None,
    timeout: int = 900,
    report_root: Path | None = None,
    runner: Callable[..., list[dict]] = run_collectors,
    registry: list[dict] | None = None,
    now: datetime | None = None,
    ledger_path: Path | None = None,
    lock_path: Path | None = None,
    retry_attempts: int = 2,
    retry_backoff_sec: float = 1.0,
    run_id_override: str | None = None,
    auto_import: bool = False,
    allow_partial_import: bool = False,
    importer: Callable[[Path], dict] = import_collector_output,
) -> dict:
    """Run one scheduled batch and persist an explicit running/completed state."""
    if retry_attempts < 1:
        raise ValueError("retry_attempts must be at least 1")
    started = now or utc_now()
    report_root = report_root or project_root() / "data" / "collection-runs"
    report_root.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_path or report_root / "ledger.sqlite3"
    lock_path = lock_path or report_root / ".collector-service.lock"
    with service_lock(lock_path):
        report_name = run_id_override or run_id(started)
        report_dir = report_root / report_name
        if report_dir.exists():
            report_dir = report_root / f"{report_name}-{time.time_ns()}"
        report_dir.mkdir(parents=True, exist_ok=True)
        state_path = report_dir / "state.json"
        state = {
            "run_id": report_dir.name,
            "run_kind": "scheduled-service",
            "status": "running",
            "started_at": started.isoformat(),
            "finished_at": None,
            "collector_ids": ids or [],
            "excluded_collector_ids": exclude_ids or [],
            "topic": topic,
            "timeout_per_collector_sec": timeout,
            "auto_import": auto_import,
            "allow_partial_import": allow_partial_import,
            "import_status": "pending" if auto_import else "not_requested",
            "snapshot_id": None,
        }
        _write_json(state_path, state)
        ledger = CollectorLedger(ledger_path)
        ledger.recover_unfinished(finished_at=state["started_at"], exclude_run_id=report_dir.name)
        ledger.run_started(
            run_id=report_dir.name,
            run_kind=state["run_kind"],
            started_at=state["started_at"],
            topic=topic,
            timeout_sec=timeout,
        )
        ledger.event(run_id=report_dir.name, event_type="run_started", status="running", payload=state)
        live_results: dict[str, dict] = {}

        def on_progress(item: dict) -> None:
            """Persist per-collector progress while the batch is still running."""
            collector_id = str(item.get("id", "unknown"))
            live_results[collector_id] = item
            if item.get("status") == "running":
                ledger.event(
                    run_id=report_dir.name,
                    event_type="collector_started",
                    collector_id=collector_id,
                    status="running",
                    payload=item,
                )
            else:
                ledger.event(
                    run_id=report_dir.name,
                    event_type="collector_finished",
                    collector_id=collector_id,
                    status=_result_status(item),
                    message=_result_message(item),
                    payload=item,
                )
            partial = {
                **state,
                "status": "running",
                "results": list(live_results.values()),
                "expected": len(live_results),
                "completed": sum(1 for value in live_results.values() if value.get("status") != "running"),
                "failed": sum(1 for value in live_results.values() if value.get("status") != "running" and _result_status(value) == "failed"),
            }
            _write_json(state_path, partial)
        try:
            selected_registry = registry
            if selected_registry is None:
                selected_registry = load_registry(project_root() / "collectors" / "collectors.json")
            if runner is run_collectors:
                results = runner(
                    ids or [],
                    topic,
                    timeout,
                    registry=selected_registry,
                    exclude_ids=exclude_ids,
                    max_attempts=retry_attempts,
                    backoff_factor=retry_backoff_sec,
                    staging_root=collector_data_root() / ".staging" / report_dir.name,
                    progress=on_progress,
                )
            else:
                results = runner(ids or [], topic, timeout, registry=selected_registry)
            finished = utc_now()
            failed = [item for item in results if _result_status(item) == "failed"]
            status = "failed" if results and len(failed) == len(results) else "partial" if failed else "success"
            if runner is not run_collectors:
                for item in results:
                    ledger.event(
                        run_id=report_dir.name,
                        event_type="collector_finished",
                        collector_id=str(item.get("id", "unknown")),
                        status=_result_status(item),
                        message=_result_message(item),
                        payload=item,
                    )
            _archive_source_artifacts(report_dir, results, collector_data_root())
            import_status = "not_requested"
            snapshot_id = None
            import_error_type = None
            import_error_message = None
            import_result = None
            if auto_import:
                allowed = status == "success" or (status == "partial" and allow_partial_import)
                output_gate_failed = any(
                    _result_status(item) == "success" and item.get("published") is False
                    for item in results
                )
                if not results:
                    import_status = "skipped_no_results"
                elif output_gate_failed:
                    # A zero exit code is not enough when the runner reports
                    # that no new output passed the publish gate.
                    import_status = "skipped_output_gate"
                elif not allowed:
                    import_status = "skipped_collection_status"
                else:
                    ledger.event(
                        run_id=report_dir.name,
                        event_type="import_started",
                        status="running",
                        payload={"collector_status": status},
                    )
                    try:
                        import_result = importer(collector_data_root())
                        snapshot_id = import_result.get("snapshot_id")
                        import_status = "succeeded"
                        ledger.event(
                            run_id=report_dir.name,
                            event_type="import_finished",
                            status="succeeded",
                            payload=import_result,
                        )
                    except Exception as exc:
                        import_status = "failed"
                        import_error_type = type(exc).__name__
                        import_error_message = str(exc)[:1000]
                        ledger.event(
                            run_id=report_dir.name,
                            event_type="import_failed",
                            status="failed",
                            message=import_error_message,
                            payload={"error_type": import_error_type},
                        )
            report = {
                **state,
                "status": status,
                "finished_at": finished.isoformat(),
                "expected": len(results),
                "completed": len(results) - len(failed),
                "failed": len(failed),
                "results": results,
                "import_status": import_status,
                "snapshot_id": snapshot_id,
                "import_result": import_result,
                "import_error_type": import_error_type,
                "import_error_message": import_error_message,
            }
            _write_json(report_dir / "results.json", report)
            _write_json(state_path, report)
            ledger.run_finished(
                run_id=report_dir.name,
                status=status,
                finished_at=report["finished_at"],
                expected=report["expected"],
                completed=report["completed"],
                failed=report["failed"],
                import_status=import_status,
                snapshot_id=snapshot_id,
                import_error_type=import_error_type,
                import_error_message=import_error_message,
            )
            ledger.event(run_id=report_dir.name, event_type="run_finished", status=status, payload=report)
            return report
        except Exception as exc:
            finished = utc_now()
            report = {
                **state,
                "status": "failed",
                "finished_at": finished.isoformat(),
                "expected": 0,
                "completed": 0,
                "failed": 0,
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:1000],
                "results": [],
                "import_status": "not_attempted_collection_error" if auto_import else "not_requested",
                "snapshot_id": None,
                "import_error_type": None,
                "import_error_message": None,
            }
            _write_json(report_dir / "results.json", report)
            _write_json(state_path, report)
            ledger.run_finished(
                run_id=report_dir.name,
                status="failed",
                finished_at=report["finished_at"],
                expected=0,
                completed=0,
                failed=0,
                error_type=report["error_type"],
                error_message=report["error_message"],
                import_status=report["import_status"],
            )
            ledger.event(
                run_id=report_dir.name,
                event_type="run_failed",
                status="failed",
                message=report["error_message"],
                payload={"error_type": report["error_type"]},
            )
            return report


def service_loop(
    *,
    interval_sec: int,
    ids: list[str] | None = None,
    exclude_ids: list[str] | None = None,
    topic: str | None = None,
    timeout: int = 900,
    once: bool = False,
    report_root: Path | None = None,
    runner: Callable[..., list[dict]] = run_collectors,
    registry: list[dict] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    ledger_path: Path | None = None,
    lock_path: Path | None = None,
    retry_attempts: int = 2,
    retry_backoff_sec: float = 1.0,
    auto_import: bool = False,
    allow_partial_import: bool = False,
) -> None:
    while True:
        execute_once(
            ids=ids,
            exclude_ids=exclude_ids,
            topic=topic,
            timeout=timeout,
            report_root=report_root,
            runner=runner,
            registry=registry,
            ledger_path=ledger_path,
            lock_path=lock_path,
            retry_attempts=retry_attempts,
            retry_backoff_sec=retry_backoff_sec,
            auto_import=auto_import,
            allow_partial_import=allow_partial_import,
        )
        if once:
            return
        sleep(max(1, interval_sec))


def main() -> int:
    parser = argparse.ArgumentParser(description="GeoAgent 常驻采集服务")
    parser.add_argument("--once", action="store_true", help="只执行一轮，便于调试")
    parser.add_argument("--interval-sec", type=int, default=86400, help="两轮采集间隔，默认 24 小时")
    parser.add_argument("--id", action="append", default=[], help="只运行指定采集器，可重复")
    parser.add_argument("--exclude-id", action="append", default=[], help="排除指定采集器，可重复")
    parser.add_argument("--topic", help="只运行指定主题")
    parser.add_argument("--timeout", type=int, default=900, help="单采集器超时秒数")
    parser.add_argument("--report-root", type=Path, help="运行报告根目录")
    parser.add_argument("--ledger-path", type=Path, help="SQLite 账本路径")
    parser.add_argument("--lock-path", type=Path, help="服务锁路径")
    parser.add_argument("--retry-attempts", type=int, default=2, help="单采集器最大尝试次数")
    parser.add_argument("--retry-backoff-sec", type=float, default=1.0, help="重试指数退避初始秒数")
    parser.add_argument("--auto-import", action="store_true", help="完整采集成功后自动导入不可变快照")
    parser.add_argument("--allow-partial-import", action="store_true", help="允许部分成功运行进入导入门禁")
    args = parser.parse_args()
    service_loop(
        interval_sec=args.interval_sec,
        ids=args.id,
        exclude_ids=args.exclude_id,
        topic=args.topic,
        timeout=args.timeout,
        once=args.once,
        report_root=args.report_root,
        ledger_path=args.ledger_path,
        lock_path=args.lock_path,
        retry_attempts=args.retry_attempts,
        retry_backoff_sec=args.retry_backoff_sec,
        auto_import=args.auto_import,
        allow_partial_import=args.allow_partial_import,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
