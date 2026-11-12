import json
import os
from datetime import datetime, timezone

import pytest

from backend import collector_config
from backend.collector_ledger import CollectorLedger, service_lock
from scripts.collector_service import execute_once, service_loop


@pytest.fixture(autouse=True)
def isolated_collector_settings(tmp_path, monkeypatch):
    """整批运行会读取排除列表；测试不读仓库里的真实配置。"""
    monkeypatch.setattr(collector_config, "CONFIG_PATH", tmp_path / "settings" / "collector-settings.json")
    monkeypatch.setattr(collector_config, "project_root", lambda: tmp_path / "settings")


def test_execute_once_writes_running_then_finished_report(tmp_path):
    def runner(ids, topic, timeout, *, registry):
        assert ids == ["demo"]
        assert topic == "usiran"
        assert timeout == 30
        return [
            {"id": "demo", "returncode": 0, "started_at": "2026-10-02T09:00:00+00:00"},
            {"id": "broken", "returncode": "timeout"},
        ]

    report = execute_once(
        ids=["demo"],
        topic="usiran",
        timeout=30,
        report_root=tmp_path,
        runner=runner,
        registry=[],
        now=datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc),
    )

    assert report["status"] == "partial"
    report_dir = tmp_path / "20261002T090000Z"
    assert json.loads((report_dir / "state.json").read_text()) ["status"] == "partial"
    saved = json.loads((report_dir / "results.json").read_text())
    assert saved["failed"] == 1
    assert len(saved["results"]) == 2
    from backend.collector_ledger import CollectorLedger

    events = CollectorLedger().events(report["run_id"])
    assert [event["event_type"] for event in events] == [
        "run_started",
        "collector_finished",
        "collector_finished",
        "run_finished",
    ]
    assert events[2]["status"] == "failed"


def test_execute_once_records_service_exception(tmp_path):
    def runner(*args, **kwargs):
        raise RuntimeError("runner unavailable")

    report = execute_once(report_root=tmp_path, runner=runner, registry=[])

    assert report["status"] == "failed"
    assert report["error_type"] == "RuntimeError"
    assert report["error_message"] == "runner unavailable"


def test_service_loop_once_does_not_sleep(tmp_path):
    calls = []

    def runner(*args, **kwargs):
        calls.append("run")
        return []

    def sleep(_):
        calls.append("sleep")

    service_loop(interval_sec=60, report_root=tmp_path, runner=runner, registry=[], once=True, sleep=sleep)

    assert calls == ["run"]


def test_service_lock_rejects_live_owner_and_releases(tmp_path):
    lock_path = tmp_path / "service.lock"
    with service_lock(lock_path):
        assert lock_path.exists()
        with pytest.raises(RuntimeError, match="lock is held"):
            with service_lock(lock_path):
                pass
    assert not lock_path.exists()


def test_execute_once_records_runner_exception_in_ledger(tmp_path):
    def runner(*args, **kwargs):
        raise RuntimeError("runner unavailable")

    report = execute_once(report_root=tmp_path, runner=runner, registry=[])

    from backend.collector_ledger import CollectorLedger

    ledger = CollectorLedger()
    assert ledger.events(report["run_id"])[-1]["event_type"] == "run_failed"


def test_execute_once_imports_only_after_full_collection_success(tmp_path):
    imported = []

    def runner(*args, **kwargs):
        return [{"id": "demo", "returncode": 0}]

    def importer(root):
        imported.append(root)
        return {"snapshot_id": "snapshot-1", "created": True, "records": 2}

    report = execute_once(
        report_root=tmp_path,
        runner=runner,
        registry=[],
        auto_import=True,
        importer=importer,
    )

    assert report["status"] == "success"
    assert report["import_status"] == "succeeded"
    assert report["snapshot_id"] == "snapshot-1"
    assert imported
    ledger = CollectorLedger()
    row = ledger.run(report["run_id"])
    assert row["import_status"] == "succeeded"
    assert row["snapshot_id"] == "snapshot-1"
    assert [event["event_type"] for event in ledger.events(report["run_id"])] == [
        "run_started",
        "collector_finished",
        "import_started",
        "import_finished",
        "run_finished",
    ]


def test_execute_once_keeps_partial_collection_out_of_import_by_default(tmp_path):
    called = []

    def runner(*args, **kwargs):
        return [
            {"id": "ok", "returncode": 0},
            {"id": "broken", "returncode": "timeout"},
        ]

    def importer(root):
        called.append(root)
        raise AssertionError("partial output must not be imported by default")

    report = execute_once(
        report_root=tmp_path,
        runner=runner,
        registry=[],
        auto_import=True,
        importer=importer,
    )

    assert report["status"] == "partial"
    assert report["import_status"] == "skipped_collection_status"
    assert report["snapshot_id"] is None
    assert called == []


def test_execute_once_does_not_import_when_publish_gate_reports_empty_output(tmp_path):
    called = []

    def runner(*args, **kwargs):
        return [{"id": "demo", "returncode": 0, "published": False}]

    def importer(root):
        called.append(root)
        return {"snapshot_id": "must-not-exist"}

    report = execute_once(
        report_root=tmp_path,
        runner=runner,
        registry=[],
        auto_import=True,
        importer=importer,
    )

    assert report["status"] == "success"
    assert report["import_status"] == "skipped_output_gate"
    assert report["snapshot_id"] is None
    assert called == []


def test_execute_once_records_import_failure_separately(tmp_path):
    def runner(*args, **kwargs):
        return [{"id": "demo", "returncode": 0}]

    def importer(root):
        raise ValueError("schema gate rejected output")

    report = execute_once(
        report_root=tmp_path,
        runner=runner,
        registry=[],
        auto_import=True,
        importer=importer,
    )

    assert report["status"] == "success"
    assert report["import_status"] == "failed"
    assert report["import_error_type"] == "ValueError"
    ledger = CollectorLedger()
    row = ledger.run(report["run_id"])
    assert row["status"] == "success"
    assert row["import_status"] == "failed"
    assert ledger.events(report["run_id"])[-2]["event_type"] == "import_failed"


def test_ledger_schema_includes_import_columns():
    from backend.store import db
    ledger = CollectorLedger()
    with db() as connection:
        columns = {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='collector_runs'")}
    assert {"import_status", "snapshot_id", "import_error_type", "import_error_message"} <= columns
    assert ledger.runs() == []


def test_ledger_marks_interrupted_runs_as_abandoned(tmp_path):
    ledger = CollectorLedger()
    ledger.run_started(
        run_id="old-run",
        run_kind="scheduled-service",
        started_at="2026-10-02T09:00:00+00:00",
        topic=None,
        timeout_sec=30,
    )
    recovered = ledger.recover_unfinished(finished_at="2026-10-02T09:01:00+00:00")

    assert recovered == ["old-run"]
    row = ledger.run("old-run")
    assert row["status"] == "abandoned"
    assert row["error_type"] == "SERVICE_INTERRUPTED"
    assert row["import_status"] == "not_attempted_interrupted"
    assert ledger.events("old-run")[-1]["event_type"] == "run_abandoned"


def test_failed_run_archive_never_borrows_previous_success(tmp_path):
    from scripts.collector_service import _archive_source_artifacts
    import json
    live=tmp_path/'data'/'demo'/'output';live.mkdir(parents=True)
    (live/'_source_status.json').write_text('{"collection_status":"ok","run_at":"old"}')
    report=tmp_path/'runs'/'new'
    _archive_source_artifacts(report,[{'id':'demo','published':False}],tmp_path/'data')
    target=report/'demo'/'source-status'
    assert not (target/'_source_status.json').exists()
    assert json.loads((target/'_source_artifact.json').read_text())['status']=='missing'
    _archive_source_artifacts(report,[{'id':'demo','published':False,'source_diagnostics':{'_source_status.json':{'collection_status':'failed','run_at':'new'}}}],tmp_path/'data')
    assert json.loads((target/'_source_status.json').read_text())['run_at']=='new'


def test_full_batch_with_all_excluded_skips_import(tmp_path):
    """单独定时已取消：整批全部被排除时没有结果，不再为并入管线结果而导入。"""
    imported = []
    registry = [{"id": "a", "enabled": True, "topics": ["usiran"]}]
    report = execute_once(report_root=tmp_path, registry=registry, exclude_ids=["a"], auto_import=True,
                          importer=lambda root: imported.append(root))
    assert not imported and report["import_status"] == "skipped_no_results"
    assert report["excluded_collector_ids"] == ["a"]


def test_batch_exclusion_does_not_consult_pipelines(tmp_path, monkeypatch):
    import scripts.collector_service as service
    assert not hasattr(service, "_with_scheduled_builtin") and not hasattr(service, "_backfill_pipeline_runs")
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "true")
    report = execute_once(report_root=tmp_path, registry=[], exclude_ids=None, runner=lambda *a, **k: [], timeout=5)
    assert report["excluded_collector_ids"] == []


def test_explicit_ids_with_no_results_still_skip_import(tmp_path):
    called = []
    report = execute_once(report_root=tmp_path, registry=[], ids=["zzz"], auto_import=True,
                          runner=lambda *a, **k: [], importer=lambda r: called.append(r))
    assert report["import_status"] == "skipped_no_results" and not called


def test_service_lock_left_by_previous_container_is_reclaimed(tmp_path, monkeypatch):
    # A restarted container's web server is PID 1 again, so the stale lock's PID looks alive.
    lock_path = tmp_path / "service.lock"
    lock_path.write_text(json.dumps({"pid": os.getpid(), "created_at": "2026-11-05T08:20:18+00:00"}))
    with service_lock(lock_path):
        assert json.loads(lock_path.read_text())["pid"] == os.getpid()
    assert not lock_path.exists()


def test_service_lock_reclaims_pid_now_owned_by_another_process(tmp_path, monkeypatch):
    from backend import collector_ledger
    lock_path = tmp_path / "service.lock"
    lock_path.write_text(json.dumps({"pid": 4242, "process": "boot-a:100"}))
    monkeypatch.setattr(collector_ledger, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(collector_ledger, "_process_identity", lambda pid: "boot-a:999" if pid == 4242 else None)
    with service_lock(lock_path):
        pass
    lock_path.write_text(json.dumps({"pid": 4242, "process": "boot-a:999"}))  # same process: still held
    with pytest.raises(RuntimeError, match="lock is held"):
        with service_lock(lock_path):
            pass


def _write_settings(exclude_ids):
    collector_config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    collector_config.CONFIG_PATH.write_text(json.dumps({"exclude_ids": exclude_ids}), encoding="utf-8")


def _registry_ab():
    return [{"id": "a", "enabled": True, "topics": ["usiran"]}, {"id": "b", "enabled": True, "topics": ["usiran"]}]


def test_full_batch_unions_configured_exclusions(tmp_path, monkeypatch):
    """R-T15-1：整批运行时把 collector_config 的排除列表并入 exclude_ids（Sealos 6 小时整批、参数过期的 launchd）。"""
    _write_settings(["b"])
    ran = []
    monkeypatch.setattr("scripts.collect.run_one", lambda item, **kw: ran.append(item["id"]) or {"id": item["id"], "returncode": 0})
    report = execute_once(report_root=tmp_path / "runs", registry=_registry_ab(), exclude_ids=["x"])
    assert ran == ["a"] and [r["id"] for r in report["results"]] == ["a"]
    assert report["excluded_collector_ids"] == ["x", "b"]


def test_explicit_ids_batch_ignores_configured_exclusions(tmp_path, monkeypatch):
    _write_settings(["b"])
    ran = []
    monkeypatch.setattr("scripts.collect.run_one", lambda item, **kw: ran.append(item["id"]) or {"id": item["id"], "returncode": 0})
    report = execute_once(report_root=tmp_path / "runs", registry=_registry_ab(), ids=["b"])
    assert ran == ["b"] and report["excluded_collector_ids"] == []


def test_full_batch_proceeds_when_exclusions_unreadable(tmp_path, monkeypatch, caplog):
    def broken():
        raise ValueError("interval_sec 越界")

    monkeypatch.setattr(collector_config, "load_config", broken)
    ran = []
    monkeypatch.setattr("scripts.collect.run_one", lambda item, **kw: ran.append(item["id"]) or {"id": item["id"], "returncode": 0})
    with caplog.at_level("WARNING"):
        report = execute_once(report_root=tmp_path / "runs", registry=_registry_ab(), exclude_ids=["a"])
    assert ran == ["b"] and report["excluded_collector_ids"] == ["a"] and report["status"] == "success"
    assert any("排除列表" in r.getMessage() for r in caplog.records)
