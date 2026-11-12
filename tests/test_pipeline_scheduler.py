import threading
from datetime import datetime, timedelta, timezone

import pytest

from backend import pipeline_scheduler
from backend.collector_jobs import CollectorBusyError
from backend.collector_ledger import service_lock
from backend.pipeline_scheduler import (pipelines_enabled, scheduled_builtin_ids, start_scheduler,
                                        sync_builtin_pipelines, tick)
from backend.pipeline_service import Actor, create_pipeline, get_pipeline, set_enabled
from backend.store import db
from scripts.collector_service import execute_once

ADMIN = Actor("admin", True)
NOW = datetime(2026, 11, 11, 8, 0, tzinfo=timezone.utc)
ROW = {"name": "测试管线", "description": "", "topic": "usiran", "interval_sec": 3600}
CFG = {"sources": [{"id": "rss-a", "type": "rss", "name": "RSS 源", "feed_url": "https://example.com/feed.xml",
                    "allowed_hosts": ["example.com"], "language": "en", "tier": "T2",
                    "content_policy": "metadata_only"}], "filters": {"include_any": ["iran"]}}


def resolve(host):
    return ["93.184.216.34"]


def make(name="测试管线", enabled=True, due=True):
    p = create_pipeline(ADMIN, {**ROW, "name": name}, CFG, resolve=resolve)
    if enabled:
        p = set_enabled(ADMIN, p["id"], True)
    if enabled and due:
        with db() as conn:
            conn.execute("UPDATE collection_pipelines SET next_run_at=%s WHERE id=%s", (NOW - timedelta(minutes=1), p["id"]))
    return get_pipeline(p["id"])


def registry(*ids, enabled=True):
    return [{"id": i, "name_zh": f"采集器{i}", "topics": ["ukraine", "usiran"], "entrypoint": "x.py",
             "selftest_args": [], "output_kind": "geo-v6", "enabled": enabled} for i in ids]


def test_tick_runs_due_pipeline_once():
    p = make()
    calls = []
    assert tick(NOW, run=lambda pid, **kw: calls.append((pid, kw))) == [p["id"]]
    assert calls == [(p["id"], {"trigger": "schedule", "actor": "scheduler"})]
    after = get_pipeline(p["id"])
    assert after["next_run_at"] == NOW + timedelta(seconds=3600)
    assert tick(NOW, run=lambda pid, **kw: calls.append(pid)) == []


def test_two_concurrent_ticks_run_once():
    p = make()
    calls, barrier = [], threading.Barrier(2)

    def run(pid, **kw):
        calls.append(pid)
        threading.Event().wait(0.3)

    def worker():
        barrier.wait()
        tick(NOW, run=run)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert calls == [p["id"]]


def test_busy_restores_next_run_at():
    p = make()
    original = p["next_run_at"]

    def busy(pid, **kw):
        raise CollectorBusyError("lock is held")

    assert tick(NOW, run=busy) == []
    assert get_pipeline(p["id"])["next_run_at"] == original


def test_other_exception_does_not_stop_tick():
    a, b = make("甲"), make("乙")
    calls = []

    def run(pid, **kw):
        calls.append(pid)
        if len(calls) == 1:
            raise RuntimeError("boom")

    assert len(tick(NOW, run=run)) == 2 and sorted(calls) == sorted([a["id"], b["id"]])


def test_running_run_skips_pipeline():
    p = make()
    with db() as conn:
        conn.execute("INSERT INTO collection_pipeline_runs(run_id,pipeline_id,version,trigger,status,started_at,data_timestamp) "
                     "VALUES ('r1',%s,1,'manual','running',%s,%s)", (p["id"], NOW, NOW))
    calls = []
    assert tick(NOW, run=lambda pid, **kw: calls.append(pid)) == [] and calls == []


def test_disabled_or_deleted_not_run():
    off = make("停用", enabled=False)
    gone = make("删除")
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET is_deleted=true WHERE id=%s", (gone["id"],))
        conn.execute("UPDATE collection_pipelines SET next_run_at=%s WHERE id=%s", (NOW - timedelta(hours=1), off["id"]))
    calls = []
    assert tick(NOW, run=lambda pid, **kw: calls.append(pid)) == [] and calls == []


def test_unscheduled_pipeline_gets_null_next_run():
    p = make()
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET interval_sec=NULL WHERE id=%s", (p["id"],))
    tick(NOW, run=lambda pid, **kw: None)
    assert get_pipeline(p["id"])["next_run_at"] is None


def test_builtin_uses_run_builtin(monkeypatch):
    sync_builtin_pipelines(registry("demo"))
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET enabled=true,interval_sec=3600,next_run_at=%s WHERE id='demo'",
                     (NOW - timedelta(minutes=1),))
    seen = []
    monkeypatch.setattr(pipeline_scheduler, "run_builtin", lambda pid, **kw: seen.append((pid, kw)))
    assert tick(NOW) == ["demo"]
    assert seen == [("demo", {"trigger": "schedule", "actor": "scheduler"})]


def test_sync_builtin_creates_disabled_rows_and_is_idempotent():
    reg = registry("c1", "c2") + registry("c3", enabled=False)
    sync_builtin_pipelines(reg)
    sync_builtin_pipelines(reg)
    with db() as conn:
        rows = conn.execute("SELECT * FROM collection_pipelines WHERE kind='builtin' ORDER BY id").fetchall()
        versions = conn.execute("SELECT * FROM collection_pipeline_versions ORDER BY pipeline_id").fetchall()
    assert [r["id"] for r in rows] == ["c1", "c2", "c3"]
    r = rows[0]
    assert (r["name"], r["topic"], r["enabled"], r["owner"], r["current_version"]) == ("采集器c1", "ukraine", False, "system", 1)
    assert len(versions) == 3 and versions[0]["source_channel"] == "migration"
    assert versions[0]["config"] == {"schema_version": "pipeline/1", "builtin": True}
    assert scheduled_builtin_ids() == []
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET enabled=true WHERE id='c2'")
    assert scheduled_builtin_ids() == ["c2"]


def test_sync_builtin_skips_name_conflict_with_user_pipeline(caplog):
    make("采集器c1", enabled=False)
    sync_builtin_pipelines(registry("c1", "c2"))
    with db() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM collection_pipelines WHERE kind='builtin'").fetchall()]
    assert ids == ["c2"]


def test_scheduled_builtin_excluded_from_batch(tmp_path, monkeypatch):
    import scripts.collect as collect
    sync_builtin_pipelines(registry("sched", "other"))
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET enabled=true WHERE id='sched'")
    ran = []

    def fake_run_one(item, **kw):
        ran.append(item["id"])
        return {"id": item["id"], "returncode": 0, "published": False}

    monkeypatch.setattr(collect, "run_one", fake_run_one)
    report = execute_once(registry=registry("sched", "other"), report_root=tmp_path / "r", runner=collect.run_collectors,
                          timeout=5)
    assert ran == ["other"] and "sched" in report["excluded_collector_ids"]
    ran.clear()
    execute_once(ids=["sched"], run_id_override="explicit", registry=registry("sched", "other"), report_root=tmp_path / "r2",
                 runner=collect.run_collectors, timeout=5)
    assert ran == ["sched"]


def test_batch_ignores_scheduler_lookup_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_scheduler, "scheduled_builtin_ids", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    report = execute_once(registry=[], report_root=tmp_path, runner=lambda *a, **k: [], timeout=5)
    assert report["status"] == "success"


def test_feature_flag(monkeypatch):
    monkeypatch.delenv("GEO_PIPELINES_ENABLED", raising=False)
    assert pipelines_enabled() is True
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "false")
    assert pipelines_enabled() is False
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "0")
    assert pipelines_enabled() is False


def test_start_scheduler_returns_daemon_thread(monkeypatch):
    stop = threading.Event()
    ticks = []
    monkeypatch.setattr(pipeline_scheduler, "tick", lambda now: ticks.append(now) or [])
    thread = start_scheduler(0.01, stop=stop)
    assert thread.daemon and thread.name == "pipeline-scheduler"
    threading.Event().wait(0.1)
    stop.set()
    thread.join(2)
    assert ticks
