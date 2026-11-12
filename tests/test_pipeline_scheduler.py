import threading

import pytest

from backend import collector_config, pipeline_scheduler
from backend.pipeline_scheduler import pipelines_enabled, sync_builtin_pipelines
from backend.pipeline_service import Actor, create_pipeline, get_pipeline
from backend.store import db

ADMIN = Actor("admin", True)
ROW = {"name": "测试管线", "description": "", "topics": ["usiran"]}
CFG = {"sources": [{"id": "rss-a", "type": "rss", "name": "RSS 源", "feed_url": "https://example.com/feed.xml",
                    "allowed_hosts": ["example.com"], "language": "en", "tier": "T2",
                    "content_policy": "metadata_only"}], "filters": {"include_any": ["iran"]}}


@pytest.fixture(autouse=True)
def collector_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_config, "CONFIG_PATH", tmp_path / "collector-settings.json")
    monkeypatch.setattr(collector_config, "project_root", lambda: tmp_path)


def resolve(host):
    return ["93.184.216.34"]


def make(name="测试管线"):
    return create_pipeline(ADMIN, {**ROW, "name": name}, CFG, resolve=resolve)


def registry(*ids, enabled=True):
    return [{"id": i, "name_zh": f"采集器{i}", "topics": ["ukraine", "usiran"], "entrypoint": "x.py",
             "selftest_args": [], "output_kind": "geo-v6", "enabled": enabled} for i in ids]


def test_scheduler_machinery_removed():
    for name in ("tick", "start_scheduler", "stop_scheduler", "scheduled_builtin_ids", "_claim", "_restore"):
        assert not hasattr(pipeline_scheduler, name)


def test_sync_builtin_mirrors_effective_enabled_and_is_idempotent():
    collector_config.save_config({"interval_sec": 86400, "exclude_ids": ["c2"]}, known_ids={"c1", "c2", "c3"})
    reg = registry("c1", "c2") + registry("c3", enabled=False)
    sync_builtin_pipelines(reg)
    sync_builtin_pipelines(reg)
    with db() as conn:
        rows = conn.execute("SELECT * FROM collection_pipelines WHERE kind='builtin' ORDER BY id").fetchall()
        versions = conn.execute("SELECT * FROM collection_pipeline_versions ORDER BY pipeline_id").fetchall()
    assert [(r["id"], r["enabled"]) for r in rows] == [("c1", True), ("c2", False), ("c3", False)]
    r = rows[0]
    assert (r["name"], r["topics"], r["owner"], r["current_version"]) == ("采集器c1", ["ukraine", "usiran"], "system", 1)
    assert len(versions) == 3 and versions[0]["source_channel"] == "migration"
    assert versions[0]["config"] == {"schema_version": "pipeline/1", "builtin": True}
    # 排除列表变化后再次同步，已存在行的 enabled 跟随有效状态
    collector_config.save_config({"interval_sec": 86400, "exclude_ids": ["c1"]}, known_ids={"c1", "c2", "c3"})
    sync_builtin_pipelines(reg)
    with db() as conn:
        rows = conn.execute("SELECT id,enabled FROM collection_pipelines WHERE kind='builtin' ORDER BY id").fetchall()
    assert [(r["id"], r["enabled"]) for r in rows] == [("c1", False), ("c2", True), ("c3", False)]


def test_sync_builtin_skips_name_conflict_with_user_pipeline():
    p = make("采集器c1")
    sync_builtin_pipelines(registry("c1", "c2"))
    with db() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM collection_pipelines WHERE kind='builtin'").fetchall()]
    assert ids == ["c2"]
    assert get_pipeline(p["id"])["enabled"] is False


def test_builtin_sync_keeps_all_registry_topics_and_skips_empty(caplog):
    reg = registry("c1") + [{**registry("c2")[0], "topics": []}]
    with caplog.at_level("WARNING"):
        sync_builtin_pipelines(reg)
    with db() as conn:
        rows = conn.execute("SELECT id,topics FROM collection_pipelines WHERE kind='builtin' ORDER BY id").fetchall()
    assert [(r["id"], r["topics"]) for r in rows] == [("c1", ["ukraine", "usiran"])]
    assert "c2" in caplog.text


def test_feature_flag(monkeypatch):
    monkeypatch.delenv("GEO_PIPELINES_ENABLED", raising=False)
    assert pipelines_enabled() is True
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "false")
    assert pipelines_enabled() is False
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "0")
    assert pipelines_enabled() is False


def _pipeline_threads():
    return [t for t in threading.enumerate() if t.name.startswith("pipeline-scheduler")]


def test_start_pipelines_recovers_and_syncs_without_thread(monkeypatch):
    from backend import app as app_module
    from backend import pipeline_runs
    calls = []
    monkeypatch.setattr(pipeline_runs, "recover_interrupted_pipeline_runs", lambda now: calls.append("recover"))
    monkeypatch.setattr(pipeline_scheduler, "sync_builtin_pipelines", lambda reg: calls.append(("sync", reg)))
    monkeypatch.setattr(app_module, "load_registry", lambda path: ["reg"])
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "false")
    app_module._start_pipelines()
    assert calls == []
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "true")
    app_module._start_pipelines()
    assert calls == ["recover", ("sync", ["reg"])] and _pipeline_threads() == []


def test_start_pipelines_step_failure_is_logged_not_raised(monkeypatch, caplog):
    from backend import app as app_module
    from backend import pipeline_runs
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "true")
    monkeypatch.setattr(pipeline_runs, "recover_interrupted_pipeline_runs",
                        lambda now: (_ for _ in ()).throw(RuntimeError("db")))
    synced = []
    monkeypatch.setattr(pipeline_scheduler, "sync_builtin_pipelines", lambda reg: synced.append(reg))
    monkeypatch.setattr(app_module, "load_registry", lambda path: [])
    app_module._start_pipelines()
    assert synced == [[]] and "恢复中断的管线运行失败" in caplog.text
