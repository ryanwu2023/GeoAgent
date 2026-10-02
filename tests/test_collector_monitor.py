import json

from backend.collector_ledger import CollectorLedger
from backend.collector_monitor import load_latest_run, load_ledger_events, load_ledger_runs, load_source_status


def test_load_latest_run_projects_report_without_claiming_missing_data(tmp_path):
    runs = tmp_path / "collection-runs" / "20261002T090000Z"
    runs.mkdir(parents=True)
    registry = tmp_path / "collectors.json"
    registry.write_text(json.dumps({"collectors": [{"id": "demo", "name_zh": "演示采集器"}]}), encoding="utf-8")
    (runs / "results.json").write_text(
        json.dumps(
            {
                "run_kind": "live-audit",
                "results": [
                    {
                        "id": "demo",
                        "returncode": 0,
                        "started_at": "2026-10-02T09:00:00+00:00",
                        "finished_at": "2026-10-02T09:01:00+00:00",
                        "duration_sec": 60,
                        "runtime_files": ["LATEST.md"],
                    },
                    {
                        "id": "timed-out",
                        "returncode": "timeout",
                        "started_at": "2026-10-02T09:00:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = load_latest_run(tmp_path / "collection-runs", registry)

    assert result["available"] is True
    assert result["run_id"] == "20261002T090000Z"
    assert result["collector_count"] == 2
    assert result["failed_count"] == 1
    assert result["collectors"][0]["name"] == "演示采集器"
    assert result["collectors"][0]["group"] == "未分类"
    assert result["collectors"][1]["status"] == "failed"
    assert result["collectors"][1]["error_type"] == "TIMEOUT"
    assert result["collectors"][1]["records"] == 0
    assert result["collectors"][0]["elapsed"] == "60秒"


def test_load_latest_run_reports_unavailable_when_no_valid_report(tmp_path):
    result = load_latest_run(tmp_path / "missing", tmp_path / "missing-registry.json")

    assert result == {
        "available": False,
        "source": "none",
        "run_id": None,
        "updated_at": None,
        "collector_count": 0,
        "failed_count": 0,
        "collectors": [],
    }


def test_load_runs_exposes_running_state_before_results_json(tmp_path):
    runs = tmp_path / "collection-runs" / "manual-running"
    runs.mkdir(parents=True)
    registry = tmp_path / "collectors.json"
    registry.write_text(json.dumps({"collectors": [{"id": "demo", "name_zh": "演示采集器"}]}), encoding="utf-8")
    (runs / "state.json").write_text(
        json.dumps({
            "run_id": "manual-running",
            "run_kind": "scheduled-service",
            "status": "running",
            "started_at": "2026-10-02T09:00:00+00:00",
            "finished_at": None,
            "import_status": "pending",
            "snapshot_id": None,
        }),
        encoding="utf-8",
    )

    result = load_latest_run(tmp_path / "collection-runs", registry)

    assert result["run_id"] == "manual-running"
    assert result["run_status"] == "running"
    assert result["updated_at"] == "2026-10-02T09:00:00+00:00"
    assert result["collectors"] == []


def test_ledger_projection_exposes_runs_and_filtered_events(tmp_path):
    root = tmp_path / "collection-runs"
    ledger = CollectorLedger(root / "ledger.sqlite3")
    ledger.run_started(
        run_id="run-1",
        run_kind="scheduled-service",
        started_at="2026-10-02T09:00:00+00:00",
        topic="usiran",
        timeout_sec=30,
    )
    ledger.event(run_id="run-1", event_type="run_started", status="running")
    ledger.event(run_id="run-1", event_type="collector_finished", collector_id="demo", status="success")
    ledger.event(run_id="run-1", event_type="collector_finished", collector_id="other", status="failed")

    runs = load_ledger_runs(root)
    events = load_ledger_events(root, "run-1", collector_id="demo")

    assert runs["available"] is True
    assert runs["runs"][0]["run_id"] == "run-1"
    assert [item["event_type"] for item in events["events"]] == ["run_started", "collector_finished"]
    assert events["events"][1]["collector_id"] == "demo"


def test_source_status_projection_merges_native_status_fetch_stats_and_health(tmp_path):
    output = tmp_path / "collector-output" / "demo" / "output"
    output.mkdir(parents=True)
    (output / "_source_status.json").write_text(
        json.dumps(
            {
                "run_at": "2026-10-02T09:00:00+00:00",
                "proxy": "无（直连）",
                "sources": [
                    {"id": "official", "state": "ok", "entries": 5, "http_status": 200},
                    {"id": "stale", "state": "ok-out-of-window", "entries": 2, "http_status": 200},
                    {"id": "disabled", "state": "disabled", "enabled": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    (output / "_fetch_stats.json").write_text(
        json.dumps({"run": "2026-10-02", "src": {"official": {"ok": True, "n": 5}, "blocked": {"ok": False, "status": 403, "err": "HTTP 403"}}}),
        encoding="utf-8",
    )
    (output / "_source_health.json").write_text(
        json.dumps({"official": {"consec_fail": 0, "last_ok": "2026-10-02"}, "blocked": {"consec_fail": 2, "last_ok": None}}),
        encoding="utf-8",
    )

    result = load_source_status(tmp_path / "collector-output", collector_id="demo")

    assert result["available"] is True
    assert result["collectors"][0]["updated_at"] == "2026-10-02T09:00:00+00:00"
    assert result["collectors"][0]["summary"] == {
        "total": 4,
        "healthy": 1,
        "stale": 1,
        "failed": 1,
        "disabled": 1,
        "entries": 7,
        "kept": 0,
    }
    blocked = next(item for item in result["collectors"][0]["sources"] if item["id"] == "blocked")
    assert blocked["state"] == "failed"
    assert blocked["consecutive_failures"] == 2

    run_output = tmp_path / "collection-runs" / "run-1" / "demo" / "source-status"
    run_output.mkdir(parents=True)
    for filename in ("_source_status.json", "_fetch_stats.json", "_source_health.json"):
        (run_output / filename).write_text((output / filename).read_text(encoding="utf-8"), encoding="utf-8")
    (run_output / "_source_artifact.json").write_text(
        json.dumps({"run_id": "run-1", "status": "available", "published_this_run": True, "files": ["_source_status.json", "_fetch_stats.json", "_source_health.json"]}),
        encoding="utf-8",
    )
    scoped = load_source_status(tmp_path / "collector-output", collector_ids={"demo"}, run_id="run-1")
    assert scoped["run_id"] == "run-1"
    assert scoped["scope"] == "run-collector-source-artifact"
    assert [item["collector_id"] for item in scoped["collectors"]] == ["demo"]
    assert scoped["collectors"][0]["artifact_run_id"] == "run-1"


def test_source_status_is_unavailable_without_published_status_artifacts(tmp_path):
    output = tmp_path / "collector-output" / "demo" / "output"
    output.mkdir(parents=True)

    result = load_source_status(tmp_path / "collector-output")

    assert result == {
        "available": False,
        "source": "none",
        "collector_id": None,
        "collectors": [],
    }
