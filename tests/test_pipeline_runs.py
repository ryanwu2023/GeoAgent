import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend import collector_config, pipeline_runs
from backend.collector_ledger import service_lock
from backend.paths import collector_data_root
from backend.pipeline_runs import (batch_pipeline_items, list_run_items, list_runs, preview_pipeline,
                                   record_batch_pipeline_runs, record_new_items, recover_interrupted_pipeline_runs,
                                   registry_item, run_trial, trial_running)
from backend.pipeline_service import (Actor, PipelineError, create_pipeline, delete_pipeline, get_pipeline, set_enabled,
                                      update_pipeline)
from backend.store import db
from scripts.collector_service import execute_once

ROOT = Path(__file__).resolve().parents[1]
ADMIN = Actor("admin", True)
ROW = {"name": "测试管线", "description": "", "topics": ["usiran"]}


def resolve(host):
    return ["93.184.216.34"]


def cfg():
    return {"sources": [
        {"id": "rss-a", "type": "rss", "name": "RSS 源", "feed_url": "https://example.com/feed.xml",
         "allowed_hosts": ["example.com"], "language": "en", "tier": "T2", "content_policy": "metadata_only"},
        {"id": "web-b", "type": "web", "name": "网页源", "entry_urls": ["https://example.org/news"],
         "allowed_hosts": ["example.org"], "language": "en", "tier": "T1", "content_policy": "public_fulltext"},
    ], "filters": {"include_any": ["iran"]}}


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "collector-output"
    monkeypatch.setenv("COLLECTOR_DATA_ROOT", str(root))
    return root


def make(enabled=True, name=None):
    p = create_pipeline(ADMIN, {**ROW, "name": name or ROW["name"]}, cfg(), resolve=resolve)
    if enabled:
        p = set_enabled(ADMIN, p["id"], True)
    return p


def rec(n, *, published="2026-11-0{n}T08:00:00+00:00", source="rss-a"):
    return {"id": f"r{n}", "title": f"标题 {n}", "link": f"https://example.com/a/{n}", "source_id": source,
            "language": "en", "published": published.format(n=n) if published else None,
            "topics": ["usiran"], "dimensions": {"usiran": "diplomacy"},
            "matched_discovery_terms": ["iran"], "matched_admission_terms": ["talks", "iran"]}


def write_output(out, records, collection_status="ok"):
    out.mkdir(parents=True, exist_ok=True)
    (out / "geo-v6.json").write_text(json.dumps(
        {"schema_version": "geo.v6/1", "collection_status": collection_status, "records": records}), encoding="utf-8")
    (out / "_collection_report.json").write_text(json.dumps(
        {"collection_status": collection_status, "records": len(records), "candidates": 9, "accepted": len(records),
         "excluded": 2, "errors": 0, "restricted": 0, "duplicates_merged": 0,
         "sources": {"rss-a": {"state": "ok"}, "web-b": {"state": "failed" if collection_status == "partial" else "ok"}}}),
        encoding="utf-8")


def fake_run_one(records, *, collection_status="ok", published=True, calls=None):
    """测试运行用的假 run_one：把产物写到传入的 data_root 下。"""
    def run(item, *, timeout, data_root):
        if calls is not None:
            calls.append({"item": item, "timeout": timeout, "data_root": data_root})
        if published:
            write_output(Path(data_root) / item["id"] / "output", records, collection_status)
        return {"id": item["id"], "returncode": 0 if published else 1, "published": published,
                "stderr_tail": "" if published else "boom"}
    return run


def trial(p, records, *, actor="admin", **kw):
    return run_trial(p["id"], actor=actor, runner=fake_run_one(records, **kw))


def count(table):
    with db() as conn:
        return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def test_trial_first_run_all_items_are_new(data_root):
    p = make(enabled=False)
    calls = []
    run = run_trial(p["id"], actor="admin",
                    runner=fake_run_one([rec(1), rec(2), rec(3, published=None)], calls=calls))
    assert run["status"] == "success" and run["snapshot_id"] is None and run["error"] is None
    assert run["trigger"] == "trial" and run["version"] == 1 and run["created_by"] == "admin"
    assert run["stats"]["new_items"] == 3 and run["stats"]["candidates"] == 9
    assert run["stats"]["import_status"] == "not_imported" and run["stats"]["sources"]["rss-a"]["state"] == "ok"
    assert run["data_timestamp"] == run["started_at"] and run["finished_at"] is not None
    call = calls[0]
    trial_root = data_root / ".pipeline-trials"
    assert call["data_root"] == trial_root and call["item"]["id"] == f"pipeline-{p['id']}"
    config_path = Path(call["item"]["env"]["GEO_PIPELINE_CONFIG"])
    assert config_path == trial_root / f"pipeline-{p['id']}" / "state" / "pipeline-config.json"
    monitor = json.loads(config_path.read_text(encoding="utf-8"))
    assert monitor["topics"] == ["usiran"] and [s["id"] for s in monitor["sources"]] == ["rss-a", "web-b"]
    assert not (data_root / f"pipeline-{p['id']}").exists()  # 正式产物与状态目录不受影响

    after = get_pipeline(p["id"])
    assert after["last_new_items"] == 3 and after["last_status"] == "success" and after["last_run_id"] == run["run_id"]
    assert after["enabled"] is False
    items = list_run_items(run["run_id"])
    assert [i["seq"] for i in items] == [1, 2, 3]
    assert [i["record_id"] for i in items] == ["r2", "r1", "r3"]  # 发布时间倒序，无时间排最后
    first = items[0]
    assert first["source_type"] == "rss" and first["dimensions"] == {"usiran": "diplomacy"} and first["language"] == "en"
    assert first["matched_terms"] == ["iran", "talks"] and first["title"] == "标题 2"
    from backend.adapters import normalize
    assert first["evidence_id"] == normalize(rec(2), f"pipeline-{p['id']}")["id"]
    assert first["data_timestamp"] == first["published_at"]
    assert items[2]["published_at"] is None and items[2]["data_timestamp"] == run["started_at"]
    assert [r["run_id"] for r in list_runs(p["id"])] == [run["run_id"]]
    assert count("pipeline_seen_items") == 0


def test_trial_partial_report_maps_to_partial(data_root):
    p = make()
    run = trial(p, [rec(1)], collection_status="partial")
    assert run["status"] == "partial" and run["stats"]["sources"]["web-b"]["state"] == "failed"
    assert get_pipeline(p["id"])["last_status"] == "partial"


def test_trial_items_store_truncated_summary(data_root):
    p = make()
    long = {**rec(1), "summary": "摘" * 1500}
    run = trial(p, [long, rec(2)])
    by_id = {i["record_id"]: i for i in list_run_items(run["run_id"])}
    assert by_id["r1"]["summary"] == "摘" * 1000 and by_id["r2"]["summary"] == ""


def test_trial_failed_collector_does_not_read_stale_output(data_root):
    p = make()
    trial(p, [rec(1)])
    run = trial(p, [rec(1), rec(2)], published=False)
    assert run["status"] == "failed" and "boom" in run["error"] and run["stats"]["new_items"] == 0
    assert list_run_items(run["run_id"]) == []
    assert get_pipeline(p["id"])["last_status"] == "failed"


def test_trial_runner_exception_is_failed_run(data_root):
    p = make()

    def boom(item, **kw):
        raise OSError("disk full")

    run = run_trial(p["id"], actor="admin", runner=boom)
    assert run["status"] == "failed" and "disk full" in run["error"]


def test_trial_rejects_builtin_and_concurrent(data_root):
    _builtin()
    with pytest.raises(PipelineError) as e:
        run_trial("bi1", actor="admin", runner=fake_run_one([]))
    assert e.value.code == "INVALID" and e.value.message == "内置采集器随整批运行，不支持测试运行"

    p = make()
    started = datetime(2026, 11, 11, 1, 0, tzinfo=timezone.utc)
    with db() as conn:
        conn.execute("INSERT INTO collection_pipeline_runs(run_id,pipeline_id,version,trigger,status,started_at,data_timestamp) "
                     "VALUES ('t-run',%s,1,'trial','running',%s,%s)", (p["id"], started, started))
    assert trial_running(p["id"]) is True
    with pytest.raises(PipelineError) as e:
        trial(p, [rec(1)])
    assert e.value.code == "CONFLICT" and e.value.message == "该管线正在测试运行"
    assert count("collection_pipeline_runs") == 1


def test_trial_same_pipeline_lock_held_is_conflict(data_root):
    p = make()
    before = get_pipeline(p["id"])

    def locked(item, **kw):
        raise RuntimeError("collector service lock is held")

    with pytest.raises(PipelineError) as e:
        run_trial(p["id"], actor="admin", runner=locked)
    assert e.value.code == "CONFLICT"
    assert count("collection_pipeline_runs") == 0
    after = get_pipeline(p["id"])
    assert (after["last_status"], after["last_run_id"]) == (before["last_status"], before["last_run_id"])


def test_trial_run_not_imported_and_not_in_seen(data_root):
    from backend.adapters import load_bundle
    p = make()
    run = trial(p, [rec(1), rec(2)])
    trial_out = data_root / ".pipeline-trials" / f"pipeline-{p['id']}" / "output" / "geo-v6.json"
    assert trial_out.is_file()
    assert run["snapshot_id"] is None and run["stats"]["new_items"] == 2
    assert count("pipeline_seen_items") == 0
    # 正式产物树里另有一个采集器的输出：导入扫描只看到它，看不到测试目录
    write_output(data_root / "demo" / "output", [rec(5)])
    assert sorted(str(f.relative_to(data_root)) for f in data_root.glob("*/output/geo-v6.json")) == ["demo/output/geo-v6.json"]
    bundle = load_bundle(data_root)
    adapters = {a for r in bundle["records"] for a in r["adapters"]}
    assert "demo" in adapters and not any(a.startswith("pipeline-") for a in adapters)


def _fake_runner_script(source_root):
    runner_dir = source_root / "pipeline-runner"
    runner_dir.mkdir(parents=True)
    (runner_dir / "pipeline_runner.py").write_text('''
import json, os
from pathlib import Path
out = Path(os.environ["COLLECTOR_OUTPUT_ROOT"])
out.mkdir(parents=True, exist_ok=True)
record = {"id": "r1", "title": "t", "link": "https://example.com/a/1", "source_id": "rss-a", "language": "en",
          "published": "2026-11-01T08:00:00+00:00", "topics": ["usiran"], "dimensions": {"usiran": "diplomacy"}}
(out / "geo-v6.json").write_text(json.dumps({"schema_version": "geo.v6/1", "collection_status": "ok", "records": [record]}))
(out / "_collection_report.json").write_text(json.dumps({"collection_status": "ok", "records": 1}))
(out / "env.json").write_text(json.dumps({k: os.environ[k] for k in
    ("COLLECTOR_STATE_ROOT", "COLLECTOR_PREVIOUS_OUTPUT_ROOT", "GEO_PIPELINE_CONFIG")}))
''', encoding="utf-8")


def test_trial_runs_while_service_lock_held(tmp_path, data_root, monkeypatch):
    """整批进行中（持有整批服务锁和该管线的正式单采集器锁）时，测试运行用真实 run_one 仍能完成。"""
    source_root = tmp_path / "sources"
    _fake_runner_script(source_root)
    monkeypatch.setattr("scripts.collect.collector_source_root", lambda: source_root)
    p = make()
    cid = f"pipeline-{p['id']}"
    reports = tmp_path / "runs"
    with service_lock(reports / ".collector-service.lock"), \
            service_lock(data_root / ".collector-locks" / f"{cid}.lock"):
        run = run_trial(p["id"], actor="admin")
    assert run["status"] == "success" and run["stats"]["new_items"] == 1, run
    trial_root = data_root / ".pipeline-trials"
    env = json.loads((trial_root / cid / "output" / "env.json").read_text())
    assert env["COLLECTOR_STATE_ROOT"] == str(trial_root / cid / "state")
    assert env["COLLECTOR_PREVIOUS_OUTPUT_ROOT"] == str(trial_root / cid / "output")
    assert env["GEO_PIPELINE_CONFIG"] == str(trial_root / cid / "state" / "pipeline-config.json")
    assert not (data_root / cid).exists() and not list(data_root.glob("*/output/geo-v6.json"))
    assert count("pipeline_seen_items") == 0


# —— 随整批运行 ——

@pytest.fixture
def batch_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "true")
    monkeypatch.setattr(collector_config, "CONFIG_PATH", tmp_path / "settings" / "collector-settings.json")
    monkeypatch.setattr(collector_config, "project_root", lambda: tmp_path / "settings")
    return tmp_path / "runs"


def batch_runner(outputs=None, *, seen=None, unpublished=(), failed=()):
    """整批假 runner：按注册表逐项写正式产物；outputs = {采集器 id: (records, collection_status)}。"""
    outputs = outputs or {}

    def runner(ids, topic, timeout, *, registry):
        if seen is not None:
            seen.append([item["id"] for item in registry])
        results = []
        for item in registry:
            if item["id"] in failed:
                results.append({"id": item["id"], "returncode": 3, "published": False, "stderr_tail": "炸了"})
            elif item["id"] in unpublished:
                results.append({"id": item["id"], "returncode": 0, "published": False})
            else:
                records, status = outputs.get(item["id"], ([], "ok"))
                write_output(collector_data_root() / item["id"] / "output", records, status)
                results.append({"id": item["id"], "returncode": 0, "published": True})
        return results
    return runner


def batch(report_root, runner, *, importer=None, **kw):
    return execute_once(report_root=report_root, runner=runner, registry=kw.pop("registry", []), auto_import=True,
                        importer=importer or (lambda root: {"snapshot_id": "snap-1"}), **kw)


def test_full_batch_includes_enabled_configurable_pipelines(batch_env, data_root):
    on, off = make(enabled=True), make(enabled=False, name="未启用管线")
    seen = []
    batch(batch_env, batch_runner(seen=seen), registry=[{"id": "demo", "topics": ["usiran"], "enabled": True}])
    assert seen == [["demo", f"pipeline-{on['id']}"]]
    config_path = data_root / f"pipeline-{on['id']}" / "state" / "pipeline-config.json"
    assert json.loads(config_path.read_text(encoding="utf-8"))["topics"] == ["usiran"]
    assert not (data_root / f"pipeline-{off['id']}").exists()


def test_batch_pipeline_items_follow_feature_flag(batch_env, data_root, monkeypatch):
    p = make(enabled=True)
    assert [i["id"] for i in batch_pipeline_items()] == [f"pipeline-{p['id']}"]
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "false")
    assert batch_pipeline_items() == []


def test_hourly_or_explicit_ids_batch_excludes_pipelines(batch_env, data_root):
    p = make(enabled=True)
    seen = []
    report = batch(batch_env, batch_runner(seen=seen), ids=["demo"],
                   registry=[{"id": "demo", "topics": ["usiran"], "enabled": True}])
    assert seen == [["demo"]] and report["status"] == "success"
    assert count("collection_pipeline_runs") == 0 and get_pipeline(p["id"])["last_run_id"] is None


def test_batch_records_pipeline_runs_with_snapshot(batch_env, data_root):
    p = make(enabled=True)
    cid = f"pipeline-{p['id']}"
    report = batch(batch_env, batch_runner({cid: ([rec(1), rec(2)], "ok")}))
    assert report["snapshot_id"] == "snap-1" and report["status"] == "success"
    runs = list_runs(p["id"])
    assert len(runs) == 1
    run = runs[0]
    assert run["run_id"] == f"{report['run_id']}:{p['id']}"
    assert run["trigger"] == "batch" and run["status"] == "success" and run["snapshot_id"] == "snap-1"
    assert run["error"] is None and run["version"] == 1 and run["finished_at"] is not None
    assert run["stats"]["new_items"] == 2 and run["stats"]["import_status"] == "succeeded"
    assert run["stats"]["candidates"] == 9
    assert [i["record_id"] for i in list_run_items(run["run_id"])] == ["r2", "r1"]
    assert count("pipeline_seen_items") == 2
    after = get_pipeline(p["id"])
    assert after["last_run_id"] == run["run_id"] and after["last_status"] == "success" and after["last_new_items"] == 2
    # 下一次整批：同样的条目不再算新增
    report2 = batch(batch_env, batch_runner({cid: ([rec(1), rec(2), rec(3)], "partial")}),
                    now=datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc))
    run2 = {r["run_id"]: r for r in list_runs(p["id"])}[f"{report2['run_id']}:{p['id']}"]
    assert run2["status"] == "partial" and run2["snapshot_id"] == "snap-1"  # 管线部分成功不影响整批导入
    assert run2["stats"]["new_items"] == 1 and count("pipeline_seen_items") == 3


DEMO = [{"id": "demo", "topics": ["usiran"], "enabled": True}]


def test_batch_pipeline_unpublished_is_failed(batch_env, data_root):
    gated, broken = make(enabled=True), make(enabled=True, name="失败管线")
    imported = []
    report = batch(batch_env, batch_runner(unpublished={f"pipeline-{gated['id']}"},
                                           failed={f"pipeline-{broken['id']}"}),
                   registry=DEMO, importer=lambda root: imported.append(root) or {"snapshot_id": "snap-1"})
    # 管线失败或未发布不影响整批状态与导入门禁
    assert report["status"] == "success" and report["import_status"] == "succeeded" and imported
    run = list_runs(gated["id"])[0]
    assert run["status"] == "failed" and run["error"] == "采集产物未通过发布门禁"
    assert run["snapshot_id"] is None  # 本次没有产物进入快照
    assert run["stats"]["new_items"] == 0 and list_run_items(run["run_id"]) == []
    other = list_runs(broken["id"])[0]
    assert other["status"] == "failed" and "炸了" in other["error"] and other["snapshot_id"] is None
    assert count("pipeline_seen_items") == 0
    assert get_pipeline(gated["id"])["last_status"] == "failed"


def test_failing_pipeline_does_not_make_batch_partial(batch_env, data_root):
    p = make(enabled=True)
    imported = []
    report = batch(batch_env, batch_runner(failed={f"pipeline-{p['id']}"}), registry=DEMO,
                   importer=lambda root: imported.append(root) or {"snapshot_id": "snap-1"},
                   allow_partial_import=False)
    assert report["status"] == "success" and report["import_status"] == "succeeded" and imported
    assert report["snapshot_id"] == "snap-1"


def test_builtin_failure_still_gates_batch(batch_env, data_root):
    p = make(enabled=True)
    report = batch(batch_env, batch_runner(failed={"demo"}), registry=DEMO + [{"id": "ok", "topics": ["usiran"]}])
    assert report["status"] == "partial" and report["import_status"] == "skipped_collection_status"
    report = batch(batch_env, batch_runner(unpublished={"demo"}), registry=DEMO,
                   now=datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc))
    assert report["import_status"] == "skipped_output_gate"
    assert all(r["snapshot_id"] is None for r in list_runs(p["id"]))


def test_topic_only_batch_includes_pipelines_and_exclusions(batch_env, data_root):
    p = make(enabled=True)
    collector_config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    collector_config.CONFIG_PATH.write_text(json.dumps({"exclude_ids": ["demo"]}), encoding="utf-8")
    seen = []
    report = batch(batch_env, batch_runner(seen=seen), topic="usiran",
                   registry=DEMO + [{"id": "other", "topics": ["usiran"]}])
    assert f"pipeline-{p['id']}" in seen[0] and "demo" in seen[0]  # 假 runner 收到完整注册表
    assert report["excluded_collector_ids"] == ["demo"]


def test_batch_run_keeps_version_and_source_types_actually_run(batch_env, data_root):
    p = make(enabled=True)
    cid = f"pipeline-{p['id']}"

    def runner(ids, topic, timeout, *, registry):
        # 整批进行中管线被修改：来源 rss-a 改成网页源、版本 +1
        changed = cfg()
        changed["sources"][0] = {"id": "rss-a", "type": "web", "name": "改成网页", "entry_urls": ["https://example.com/n"],
                                 "allowed_hosts": ["example.com"], "language": "en", "tier": "T2",
                                 "content_policy": "metadata_only"}
        update_pipeline(ADMIN, p["id"], expected_version=1, row_patch={}, config=changed, resolve=resolve)
        return batch_runner({cid: ([rec(1)], "ok")})(ids, topic, timeout, registry=registry)

    batch(batch_env, runner)
    assert get_pipeline(p["id"])["current_version"] == 2
    run = list_runs(p["id"])[0]
    assert run["version"] == 1
    assert list_run_items(run["run_id"])[0]["source_type"] == "rss"


def test_disable_and_delete_retire_published_output(batch_env, data_root):
    from backend.adapters import load_bundle
    p = make(enabled=True)
    cid = f"pipeline-{p['id']}"
    write_output(data_root / "demo" / "output", [rec(5)])
    batch(batch_env, batch_runner({cid: ([rec(1)], "ok"), "demo": ([rec(5)], "ok")}), registry=DEMO)

    def adapters():
        return {a for r in load_bundle(data_root)["records"] for a in r["adapters"]}

    assert cid in adapters()
    set_enabled(ADMIN, p["id"], False)
    assert not (data_root / cid).exists() and cid not in adapters() and "demo" in adapters()
    retired = list((data_root / ".pipeline-retired").glob(f"{cid}-*"))
    assert len(retired) == 1 and (retired[0] / "output" / "geo-v6.json").is_file()

    set_enabled(ADMIN, p["id"], True)
    batch(batch_env, batch_runner({cid: ([rec(2)], "ok"), "demo": ([rec(5)], "ok")}), registry=DEMO,
          now=datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc))
    payload = json.loads((data_root / cid / "output" / "geo-v6.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in payload["records"]] == ["r2"] and cid in adapters()

    delete_pipeline(ADMIN, p["id"])
    assert not (data_root / cid).exists() and cid not in adapters() and "demo" in adapters()
    assert len(list((data_root / ".pipeline-retired").glob(f"{cid}-*"))) == 2


def test_retire_failure_keeps_db_change(batch_env, data_root, monkeypatch, caplog):
    import shutil
    p = make(enabled=True)
    (data_root / f"pipeline-{p['id']}" / "output").mkdir(parents=True)

    def broken(*a, **k):
        raise OSError("busy")

    monkeypatch.setattr(shutil, "move", broken)
    with caplog.at_level("WARNING"):
        assert set_enabled(ADMIN, p["id"], False)["enabled"] is False
    assert (data_root / f"pipeline-{p['id']}").exists()
    assert any("退役" in r.getMessage() for r in caplog.records)


def test_builtin_disable_does_not_retire(batch_env, data_root, monkeypatch):
    from backend import pipeline_service
    _builtin()
    monkeypatch.setattr(pipeline_service, "_registry", lambda: [{"id": "bi1", "name_zh": "内置", "topics": ["usiran"]}])
    (data_root / "bi1" / "output").mkdir(parents=True)
    set_enabled(ADMIN, "bi1", False)
    assert (data_root / "bi1" / "output").is_dir() and not (data_root / ".pipeline-retired").exists()


def test_batch_import_failure_still_records_runs(batch_env, data_root):
    p = make(enabled=True)
    cid = f"pipeline-{p['id']}"

    def bad_import(root):
        raise ValueError("坏数据")

    report = batch(batch_env, batch_runner({cid: ([rec(1)], "ok")}), importer=bad_import)
    assert report["import_status"] == "failed"
    run = list_runs(p["id"])[0]
    assert run["status"] == "success" and run["snapshot_id"] is None and run["stats"]["import_status"] == "failed"
    assert run["stats"]["new_items"] == 1 and count("pipeline_seen_items") == 1


def test_batch_pipeline_record_failure_does_not_break_batch(batch_env, data_root, monkeypatch):
    p = make(enabled=True)

    def boom(report, snapshot_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(pipeline_runs, "record_batch_pipeline_runs", boom)
    report = batch(batch_env, batch_runner({f"pipeline-{p['id']}": ([rec(1)], "ok")}))
    assert report["status"] == "success" and report["snapshot_id"] == "snap-1"


def test_record_batch_pipeline_runs_is_idempotent(batch_env, data_root):
    p = make(enabled=True)
    cid = f"pipeline-{p['id']}"
    report = batch(batch_env, batch_runner({cid: ([rec(1)], "ok")}))
    assert record_batch_pipeline_runs(report, "snap-1") == []
    assert len(list_runs(p["id"])) == 1 and count("pipeline_seen_items") == 1


def test_trial_then_batch_still_counts_new(batch_env, data_root):
    p = make(enabled=False)
    first = trial(p, [rec(1), rec(2)])
    assert first["stats"]["new_items"] == 2 and count("pipeline_seen_items") == 0
    set_enabled(ADMIN, p["id"], True)
    batch(batch_env, batch_runner({f"pipeline-{p['id']}": ([rec(1), rec(2)], "ok")}))
    run = [r for r in list_runs(p["id"]) if r["trigger"] == "batch"][0]
    assert run["stats"]["new_items"] == 2 and count("pipeline_seen_items") == 2
    again = trial(p, [rec(1), rec(2), rec(3)])
    assert again["stats"]["new_items"] == 1 and count("pipeline_seen_items") == 2


def test_record_new_items_is_idempotent(data_root):
    p = make()
    run = trial(p, [])
    with db() as conn:
        n = record_new_items(conn, pipeline_id=p["id"], run_id=run["run_id"], records=[rec(1)], source_types={"rss-a": "rss"})
        again = record_new_items(conn, pipeline_id=p["id"], run_id=run["run_id"], records=[rec(1)], source_types={"rss-a": "rss"})
    assert (n, again) == (1, 0)


def test_record_new_items_without_ledger_reads_but_never_writes(data_root):
    p = make()
    run = trial(p, [])
    with db() as conn:
        record_new_items(conn, pipeline_id=p["id"], run_id=run["run_id"], records=[rec(1)], source_types={})
    other = trial(p, [])
    with db() as conn:
        n = record_new_items(conn, pipeline_id=p["id"], run_id=other["run_id"], records=[rec(1), rec(2), rec(2)],
                             source_types={}, write_ledger=False)
    assert n == 1 and count("pipeline_seen_items") == 1
    assert [i["record_id"] for i in list_run_items(other["run_id"])] == ["r2"]


def test_preview_writes_nothing(monkeypatch, data_root):
    p = make()
    tables = ["collection_pipeline_runs", "pipeline_seen_items", "collection_pipeline_run_items", "snapshots"]
    before = {t: count(t) for t in tables}
    seen = {}

    def fake_process(command, *, cwd, env, timeout):
        seen.update(command=command, cwd=cwd, env=env, timeout=timeout)
        monitor = json.loads(Path(env["GEO_PIPELINE_CONFIG"]).read_text(encoding="utf-8"))
        seen["monitor"] = monitor
        body = {"sources": [{"source_id": s["id"], "state": "ok", "error": None, "candidates": [
            {"title": "Iran talks", "url": "https://example.com/x", "accepted": True, "reason": "", "matched": ["iran"]}]}
            for s in monitor["sources"]]}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(body), stderr="")

    monkeypatch.setattr(pipeline_runs, "_run_process", fake_process)
    result = preview_pipeline(ROW, cfg(), limit=3, timeout=30, resolve=resolve)
    assert [s["source_id"] for s in result["sources"]] == ["rss-a", "web-b"]
    assert seen["command"][0] == sys.executable and seen["command"][-2:] == ["--preview", "3"]
    assert seen["cwd"] == ROOT / "collectors" / "pipeline-runner" and seen["timeout"] == 30
    assert seen["env"]["GEO_COLLECTOR_MANAGED"] == "true" and "COLLECTOR_OUTPUT_ROOT" not in seen["env"]
    assert seen["monitor"]["topics"] == ["usiran"]
    assert not Path(seen["env"]["GEO_PIPELINE_CONFIG"]).exists()  # 临时配置已清理
    assert {t: count(t) for t in tables} == before
    assert not data_root.exists() or not any(data_root.rglob("geo-v6.json"))
    assert get_pipeline(p["id"])["last_run_id"] is None


def test_preview_maps_errors(monkeypatch):
    with pytest.raises(PipelineError) as e:
        preview_pipeline({**ROW, "topics": ["bad"]}, cfg(), resolve=resolve)
    assert e.value.code == "INVALID"

    monkeypatch.setattr(pipeline_runs, "_run_process",
                        lambda command, **kw: subprocess.CompletedProcess(command, 1, stdout="", stderr="崩溃"))
    with pytest.raises(PipelineError) as e:
        preview_pipeline(ROW, cfg(), resolve=resolve)
    assert e.value.code == "PREVIEW_FAILED"

    def timeout(command, **kw):
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr(pipeline_runs, "_run_process", timeout)
    with pytest.raises(PipelineError) as e:
        preview_pipeline(ROW, cfg(), resolve=resolve)
    assert e.value.code == "PREVIEW_FAILED" and "超时" in e.value.message


def test_recover_marks_interrupted_runs(data_root):
    p = make()
    started = datetime(2026, 11, 11, 1, 0, tzinfo=timezone.utc)
    with db() as conn:
        conn.execute("INSERT INTO collection_pipeline_runs(run_id,pipeline_id,version,trigger,status,started_at,data_timestamp) "
                     "VALUES ('run-x',%s,1,'trial','running',%s,%s)", (p["id"], started, started))
        conn.execute("UPDATE collection_pipelines SET last_status='running', last_run_id='run-x' WHERE id=%s", (p["id"],))
    now = datetime(2026, 11, 11, 2, 0, tzinfo=timezone.utc)
    assert recover_interrupted_pipeline_runs(now) == ["run-x"]
    run = list_runs(p["id"])[0]
    assert run["status"] == "failed" and run["error"] == "SERVICE_INTERRUPTED" and run["finished_at"] == now
    after = get_pipeline(p["id"])
    assert after["last_status"] == "failed" and after["last_finished_at"] == now and after["enabled"] is True
    assert recover_interrupted_pipeline_runs(now) == []


def test_registry_item_uses_shared_runner_dir(tmp_path):
    p = make()
    item = registry_item(p, tmp_path / "c.json")
    assert item == {"id": f"pipeline-{p['id']}", "name_zh": "测试管线", "topics": ["usiran"],
                    "entrypoint": "pipeline_runner.py", "source_dir": "pipeline-runner", "selftest_args": ["--selftest"],
                    "output_kind": "geo-v6", "enabled": True, "env": {"GEO_PIPELINE_CONFIG": str(tmp_path / "c.json")}}
    assert (ROOT / "collectors" / item["source_dir"] / item["entrypoint"]).is_file()


def _load_runner():
    path = ROOT / "collectors" / "pipeline-runner" / "pipeline_runner.py"
    spec = importlib.util.spec_from_file_location("pipeline_runner_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_preview_classifies_candidates_without_writing(tmp_path):
    runner = _load_runner()
    from backend.pipeline_config import to_monitor_config, validate_pipeline
    row, config = validate_pipeline(ROW, cfg(), resolve=resolve)
    base = json.loads((ROOT / "collectors" / "geopolitical-source-monitor" / "config.json").read_text(encoding="utf-8"))
    path = tmp_path / "monitor.json"
    path.write_text(json.dumps(to_monitor_config(row, config, base)), encoding="utf-8")
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feed = ("<rss><channel>" + "".join(
        f"<item><title>{t}</title><link>https://example.com/{i}</link><pubDate>{now}</pubDate></item>"
        for i, t in enumerate(["Iran and the United States resume nuclear talks with sanctions relief", "Football final tonight",
                               "Iran ceasefire talks", "Weather"])) + "</channel></rss>")

    def transport(request, timeout):
        from web import TransportResponse
        if request.full_url.startswith("https://example.com"):
            return TransportResponse(200, {"Content-Type": "application/rss+xml"}, feed.encode(), request.full_url)
        return TransportResponse(503, {}, b"", request.full_url)

    from web import PolicyHttpClient
    client = PolicyHttpClient(user_agent="t", transport=transport, sleep=lambda _: None, robots_fetcher=lambda _: "")
    result = runner.preview(path, 2, client=client)
    by_id = {s["source_id"]: s for s in result["sources"]}
    assert set(by_id) == {"rss-a", "web-b"}
    rss = by_id["rss-a"]
    assert rss["state"] == "ok" and rss["error"] is None and len(rss["candidates"]) == 2
    assert rss["candidates"][0]["accepted"] is True and "iran" in rss["candidates"][0]["matched"]
    assert rss["candidates"][1]["accepted"] is False and rss["candidates"][1]["reason"]
    assert by_id["web-b"]["state"] == "failed" and by_id["web-b"]["error"] == "http_503"
    assert list(tmp_path.iterdir()) == [path]


def test_runner_selftest_passes_offline():
    runner = ROOT / "collectors" / "pipeline-runner" / "pipeline_runner.py"
    completed = subprocess.run([sys.executable, str(runner), "--selftest"], cwd=runner.parent,
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert "自检通过" in completed.stdout


def test_runner_requires_pipeline_config(tmp_path):
    runner = ROOT / "collectors" / "pipeline-runner" / "pipeline_runner.py"
    env = {k: v for k, v in __import__("os").environ.items() if k != "GEO_PIPELINE_CONFIG"}
    env["COLLECTOR_OUTPUT_ROOT"] = str(tmp_path / "out")
    completed = subprocess.run([sys.executable, str(runner)], cwd=runner.parent, env=env,
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 2 and "configuration_required" in completed.stdout


def _builtin():
    with db() as conn:
        conn.execute("INSERT INTO collection_pipelines(id,kind,name,topics,enabled,current_version,owner,created_by) "
                     "VALUES ('bi1','builtin','内置采集器',ARRAY['usiran'],true,1,'system','system')")
        conn.execute("INSERT INTO collection_pipeline_versions(pipeline_id,version,config,source_channel) "
                     "VALUES ('bi1',1,'{\"schema_version\":\"pipeline/1\",\"builtin\":true}','migration')")


def test_manual_run_entry_points_are_gone():
    assert not hasattr(pipeline_runs, "run_pipeline") and not hasattr(pipeline_runs, "run_builtin")


def test_run_items_store_dimensions_json(data_root):
    p = make()
    with db() as conn:
        conn.execute("UPDATE collection_pipelines SET topics=ARRAY['usiran','ukraine'] WHERE id=%s", (p["id"],))
    r = rec(1)
    r["topics"] = ["usiran", "ukraine", "taiwan"]
    r["dimensions"] = {"usiran": "diplomacy", "ukraine": "aid", "taiwan": "x"}
    run = trial(p, [r, rec(2)])
    items = {i["record_id"]: i for i in list_run_items(run["run_id"])}
    assert items["r1"]["dimensions"] == {"usiran": "diplomacy", "ukraine": "aid"}
    assert items["r2"]["dimensions"] == {"usiran": "diplomacy"}
