from datetime import datetime, timedelta, timezone

from backend import collector_scheduler as cs
from backend.collector_jobs import CollectorBusyError

T0 = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)


class Job:
    run_id = "scheduled-x"


def _scheduler(tmp_path, calls, busy=False):
    def submit(**kwargs):
        if busy:
            raise CollectorBusyError("busy")
        calls.append(kwargs)
        return Job()
    config = cs.ScheduleConfig(enabled=True, hourly_interval_sec=3600, full_interval_sec=21600)
    return cs.CollectorScheduler(config, state_path=tmp_path / "s.json", submit=submit,
                                 hourly_ids=["diplomacy-monitor"])


def test_first_tick_runs_full_import_then_hourly_collect_only(tmp_path):
    calls = []
    s = _scheduler(tmp_path, calls)
    assert s.tick(T0) == "full"
    assert calls[0] == {"run_prefix": "scheduled-full", "auto_import": True, "allow_partial_import": True}
    assert s.tick(T0 + timedelta(minutes=30)) is None  # full also counts as this hour's news run
    assert s.tick(T0 + timedelta(hours=1)) == "hourly"
    assert calls[1] == {"run_prefix": "scheduled-hourly", "ids": ["diplomacy-monitor"], "auto_import": False}
    assert s.tick(T0 + timedelta(hours=6)) == "full"


def test_busy_pipeline_is_retried_on_next_tick(tmp_path):
    calls = []
    s = _scheduler(tmp_path, calls, busy=True)
    assert s.tick(T0) is None
    assert not (tmp_path / "s.json").exists()


def test_state_survives_restart(tmp_path):
    calls = []
    _scheduler(tmp_path, calls).tick(T0)
    restarted = _scheduler(tmp_path, calls)
    assert restarted.tick(T0 + timedelta(minutes=10)) is None
    assert len(calls) == 1


def test_hourly_ids_come_from_registry_cadence():
    ids = cs.hourly_collector_ids()
    assert "diplomacy-monitor" in ids and "eu-gas-storage-monitor" not in ids


def test_disabled_or_remote_mode_starts_nothing(monkeypatch):
    started = []
    monkeypatch.setattr(cs.threading, "Thread", lambda *a, **k: started.append(k) or type("T", (), {"start": lambda self: None})())
    monkeypatch.setenv("GEO_COLLECT_SCHEDULE_ENABLED", "false")
    cs.start_background()
    monkeypatch.setenv("GEO_COLLECT_SCHEDULE_ENABLED", "true")
    monkeypatch.setenv("GEO_COLLECTION_MODE", "remote")
    cs.start_background()
    assert started == []
    monkeypatch.setenv("GEO_COLLECTION_MODE", "local")
    cs.start_background()
    assert started and started[0]["name"] == "collector-schedule"
