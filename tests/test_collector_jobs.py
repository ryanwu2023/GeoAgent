import time

from backend.collector_jobs import CollectorJobManager


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
    assert not (tmp_path / "collection-runs" / ".collector-service.lock").exists()
