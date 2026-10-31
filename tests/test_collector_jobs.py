import time

from backend.collector_jobs import CollectorJobManager, recover_interrupted_runs
from backend.collector_ledger import CollectorLedger, service_lock


def test_manual_job_manager_returns_run_and_updates_background_status(tmp_path, monkeypatch):
    def fake_execute_once(**kwargs):
        assert kwargs["ids"] == ["demo"]
        assert kwargs["run_id_override"].startswith("manual-")
        return {"run_id": kwargs["run_id_override"], "status": "success"}

    monkeypatch.setattr("backend.collector_jobs.execute_once", fake_execute_once)
    manager = CollectorJobManager(report_root=tmp_path / "collection-runs")
    job = manager.submit(ids=["demo"], retry_attempts=2)

    for _ in range(20):
        if job.status == "success":
            break
        time.sleep(0.01)

    assert job.status == "success"
    assert job.report["status"] == "success"


def test_startup_recovery_marks_orphaned_runs_abandoned(tmp_path):
    root = tmp_path / "collection-runs"
    ledger = CollectorLedger()
    ledger.run_started(run_id="orphan", run_kind="scheduled-service", started_at="2026-10-15T01:00:00+00:00", topic=None, timeout_sec=30)

    recovered = recover_interrupted_runs(report_root=root, finished_at="2026-10-15T01:05:00+00:00")

    assert recovered == ["orphan"]
    assert ledger.run("orphan")["status"] == "abandoned"


def test_startup_recovery_preserves_live_collector_owner(tmp_path):
    root = tmp_path / "collection-runs"
    ledger = CollectorLedger()
    ledger.run_started(run_id="active", run_kind="scheduled-service", started_at="2026-10-15T01:00:00+00:00", topic=None, timeout_sec=30)

    with service_lock(root / ".collector-service.lock"):
        assert recover_interrupted_runs(report_root=root, finished_at="2026-10-15T01:05:00+00:00") == []

    assert ledger.run("active")["status"] == "running"
    assert not (tmp_path / "collection-runs" / ".collector-service.lock").exists()
