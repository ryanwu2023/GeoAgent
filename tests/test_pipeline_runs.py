import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import pipeline_runs
from backend.collector_jobs import CollectorBusyError
from backend.collector_ledger import service_lock
from backend.pipeline_runs import (list_run_items, list_runs, preview_pipeline, record_new_items,
                                   recover_interrupted_pipeline_runs, registry_item, run_pipeline)
from backend.pipeline_service import Actor, PipelineError, create_pipeline, get_pipeline, set_enabled
from backend.store import db
from scripts.collector_service import execute_once

ROOT = Path(__file__).resolve().parents[1]
ADMIN = Actor("admin", True)
ROW = {"name": "测试管线", "description": "", "topic": "usiran", "interval_sec": 3600}


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


def make(enabled=True):
    p = create_pipeline(ADMIN, ROW, cfg(), resolve=resolve)
    if enabled:
        p = set_enabled(ADMIN, p["id"], True)
    return p


def rec(n, *, published="2026-11-0{n}T08:00:00+00:00", source="rss-a"):
    return {"id": f"r{n}", "title": f"标题 {n}", "link": f"https://example.com/a/{n}", "source_id": source,
            "language": "en", "published": published.format(n=n) if published else None,
            "topics": ["usiran"], "dimensions": {"usiran": "diplomacy"},
            "matched_discovery_terms": ["iran"], "matched_admission_terms": ["talks", "iran"]}


def fake_executor(data_root, records, *, collection_status="ok", import_status="succeeded",
                  import_error=None, published=True, calls=None):
    def run(**kw):
        if calls is not None:
            calls.append(kw)
        item = kw["registry"][0]
        out = data_root / item["id"] / "output"
        if published:
            out.mkdir(parents=True, exist_ok=True)
            (out / "geo-v6.json").write_text(json.dumps(
                {"schema_version": "geo.v6/1", "collection_status": collection_status, "records": records}),
                encoding="utf-8")
            (out / "_collection_report.json").write_text(json.dumps(
                {"collection_status": collection_status, "records": len(records), "candidates": 9, "accepted": len(records),
                 "excluded": 2, "errors": 0, "restricted": 0, "duplicates_merged": 0,
                 "sources": {"rss-a": {"state": "ok"}, "web-b": {"state": "failed" if collection_status == "partial" else "ok"}}}),
                encoding="utf-8")
        return {"status": "success" if published else "failed",
                "results": [{"id": item["id"], "returncode": 0 if published else 1, "published": published,
                             "stderr_tail": "" if published else "boom"}],
                "import_status": import_status if published else "skipped_collection_status",
                "snapshot_id": "snap-1" if import_status == "succeeded" and published else None,
                "import_error_message": import_error}
    return run


def count(table):
    with db() as conn:
        return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def test_first_run_all_items_are_new(data_root):
    p = make()
    calls = []
    run = run_pipeline(p["id"], trigger="manual", actor="admin",
                       executor=fake_executor(data_root, [rec(1), rec(2), rec(3, published=None)], calls=calls))
    assert run["status"] == "success" and run["snapshot_id"] is None and run["error"] is None
    assert run["trigger"] == "manual" and run["version"] == 1 and run["created_by"] == "admin"
    assert run["stats"]["new_items"] == 3 and run["stats"]["candidates"] == 9
    assert run["stats"]["import_status"] == "deferred" and run["stats"]["sources"]["rss-a"]["state"] == "ok"
    assert run["data_timestamp"] == run["started_at"] and run["finished_at"] is not None
    kw = calls[0]
    item = kw["registry"][0]
    assert kw["ids"] == [item["id"]] == [f"pipeline-{p['id']}"]
    assert kw["run_id_override"] == run["run_id"] and kw["auto_import"] is False
    config_path = Path(item["env"]["GEO_PIPELINE_CONFIG"])
    assert config_path == data_root / f"pipeline-{p['id']}" / "state" / "pipeline-config.json"
    monitor = json.loads(config_path.read_text(encoding="utf-8"))
    assert monitor["topics"] == ["usiran"] and [s["id"] for s in monitor["sources"]] == ["rss-a", "web-b"]

    after = get_pipeline(p["id"])
    assert after["last_new_items"] == 3 and after["last_status"] == "success" and after["last_run_id"] == run["run_id"]
    assert after["last_finished_at"] == run["finished_at"]
    assert after["next_run_at"] == run["finished_at"] + timedelta(seconds=3600)
    items = list_run_items(run["run_id"])
    assert [i["seq"] for i in items] == [1, 2, 3]
    assert [i["record_id"] for i in items] == ["r2", "r1", "r3"]  # 发布时间倒序，无时间排最后
    first = items[0]
    assert first["source_type"] == "rss" and first["dimension"] == "diplomacy" and first["language"] == "en"
    assert first["matched_terms"] == ["iran", "talks"] and first["title"] == "标题 2"
    from backend.adapters import normalize
    assert first["evidence_id"] == normalize(rec(2), f"pipeline-{p['id']}")["id"]
    assert first["data_timestamp"] == first["published_at"]
    assert items[2]["published_at"] is None and items[2]["data_timestamp"] == run["started_at"]
    assert [r["run_id"] for r in list_runs(p["id"])] == [run["run_id"]]


def test_second_run_only_new_items(data_root):
    p = make()
    run_pipeline(p["id"], trigger="schedule", actor=None, executor=fake_executor(data_root, [rec(1), rec(2), rec(3)]))
    web = rec(4, source="web-b")
    run = run_pipeline(p["id"], trigger="schedule", actor=None,
                       executor=fake_executor(data_root, [rec(1), rec(2), rec(3), web]))
    assert run["created_by"] is None and run["stats"]["new_items"] == 1
    items = list_run_items(run["run_id"])
    assert [(i["seq"], i["record_id"], i["source_type"]) for i in items] == [(1, "r4", "web")]
    assert get_pipeline(p["id"])["last_new_items"] == 1
    assert count("pipeline_seen_items") == 4
    assert len(list_runs(p["id"])) == 2


def test_rerun_without_new_content_records_zero(data_root):
    p = make()
    records = [rec(1), rec(2), rec(3)]
    run_pipeline(p["id"], trigger="manual", actor="admin", executor=fake_executor(data_root, records))
    run = run_pipeline(p["id"], trigger="manual", actor="admin", executor=fake_executor(data_root, records))
    assert list_run_items(run["run_id"]) == []
    assert get_pipeline(p["id"])["last_new_items"] == 0 and run["stats"]["new_items"] == 0


def test_busy_collector_leaves_no_running_row(tmp_path, data_root):
    p = make()
    before = get_pipeline(p["id"])
    reports = tmp_path / "runs"

    def executor(**kw):
        return execute_once(report_root=reports, **kw)

    with service_lock(reports / ".collector-service.lock"):
        with pytest.raises(CollectorBusyError):
            run_pipeline(p["id"], trigger="schedule", actor=None, executor=executor)
    assert count("collection_pipeline_runs") == 0
    after = get_pipeline(p["id"])
    assert (after["last_status"], after["last_run_id"], after["next_run_at"]) == \
           (before["last_status"], before["last_run_id"], before["next_run_at"])


def test_partial_report_maps_to_partial(data_root):
    p = make()
    run = run_pipeline(p["id"], trigger="manual", actor="admin",
                       executor=fake_executor(data_root, [rec(1)], collection_status="partial"))
    assert run["status"] == "partial" and run["stats"]["sources"]["web-b"]["state"] == "failed"
    assert get_pipeline(p["id"])["last_status"] == "partial"


def test_pipeline_run_does_not_import(data_root):
    p = make()
    calls = []
    run = run_pipeline(p["id"], trigger="manual", actor="admin",
                       executor=fake_executor(data_root, [rec(1)], calls=calls))
    assert calls[0]["auto_import"] is False
    assert run["status"] == "success" and run["snapshot_id"] is None
    assert run["stats"]["import_status"] == "deferred"


def test_import_failure_no_longer_affects_pipeline_run(data_root):
    p = make()
    run = run_pipeline(p["id"], trigger="manual", actor="admin",
                       executor=fake_executor(data_root, [rec(1)], import_status="failed", import_error="坏数据"))
    assert run["status"] == "success" and run["error"] is None and run["snapshot_id"] is None


def test_run_items_store_truncated_summary(data_root):
    p = make()
    long = {**rec(1), "summary": "摘" * 1500}
    run = run_pipeline(p["id"], trigger="manual", actor="admin",
                       executor=fake_executor(data_root, [long, rec(2)]))
    by_id = {i["record_id"]: i for i in list_run_items(run["run_id"])}
    assert by_id["r1"]["summary"] == "摘" * 1000 and by_id["r2"]["summary"] == ""


def test_backfill_merged_runs(data_root):
    p = make()
    run = run_pipeline(p["id"], trigger="manual", actor="admin", executor=fake_executor(data_root, [rec(1)]))
    failed = run_pipeline(p["id"], trigger="manual", actor="admin",
                          executor=fake_executor(data_root, [], published=False))
    after_cut = datetime.now(timezone.utc) - timedelta(hours=1)
    assert pipeline_runs.backfill_merged_runs("s1", imported_before=after_cut) == 0
    n = pipeline_runs.backfill_merged_runs("s1", imported_before=datetime.now(timezone.utc))
    assert n == 1
    rows = {r["run_id"]: r for r in list_runs(p["id"])}
    assert rows[run["run_id"]]["snapshot_id"] == "s1" and rows[run["run_id"]]["updated_at"] is not None
    assert rows[run["run_id"]]["data_timestamp"] == run["data_timestamp"]
    assert rows[failed["run_id"]]["snapshot_id"] is None
    assert pipeline_runs.backfill_merged_runs("s2", imported_before=datetime.now(timezone.utc)) == 0


def test_batch_import_backfills_pipeline_runs(data_root, tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "true")
    p = make()
    run = run_pipeline(p["id"], trigger="manual", actor="admin", executor=fake_executor(data_root, [rec(1)]))

    def importer(root):
        return {"snapshot_id": "s1", "created": True}

    report = execute_once(report_root=tmp_path / "r", runner=lambda *a, **k: [{"id": "x", "returncode": 0}],
                          registry=[], auto_import=True, importer=importer)
    assert report["import_status"] == "succeeded"
    assert list_runs(p["id"])[0]["snapshot_id"] == "s1"
    later = run_pipeline(p["id"], trigger="manual", actor="admin", executor=fake_executor(data_root, [rec(2)]))
    assert later["snapshot_id"] is None
    assert {r["run_id"]: r["snapshot_id"] for r in list_runs(p["id"])}[run["run_id"]] == "s1"


def test_run_finished_after_import_start_is_not_backfilled(data_root):
    p = make()
    cut = datetime.now(timezone.utc)
    run = run_pipeline(p["id"], trigger="manual", actor="admin", executor=fake_executor(data_root, [rec(1)]))
    assert run["finished_at"] > cut
    assert pipeline_runs.backfill_merged_runs("s1", imported_before=cut) == 0
    assert list_runs(p["id"])[0]["snapshot_id"] is None


def test_backfill_failure_does_not_fail_batch(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "true")

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(pipeline_runs, "backfill_merged_runs", boom)
    report = execute_once(report_root=tmp_path, runner=lambda *a, **k: [{"id": "x", "returncode": 0}],
                          registry=[], auto_import=True, importer=lambda root: {"snapshot_id": "s1"})
    assert report["status"] == "success" and report["import_status"] == "succeeded"
    assert any(r.levelname == "WARNING" and "回填" in r.getMessage() for r in caplog.records)


def test_failed_collector_does_not_read_stale_output(data_root):
    p = make()
    run_pipeline(p["id"], trigger="manual", actor="admin", executor=fake_executor(data_root, [rec(1)]))
    run = run_pipeline(p["id"], trigger="manual", actor="admin",
                       executor=fake_executor(data_root, [rec(1), rec(2)], published=False))
    assert run["status"] == "failed" and run["error"] and run["stats"]["new_items"] == 0
    assert list_run_items(run["run_id"]) == []
    assert get_pipeline(p["id"])["last_status"] == "failed"


def test_disabled_pipeline_keeps_next_run_empty(data_root):
    p = make(enabled=False)
    run_pipeline(p["id"], trigger="manual", actor="admin", executor=fake_executor(data_root, [rec(1)]))
    after = get_pipeline(p["id"])
    assert after["next_run_at"] is None and after["last_status"] == "success"


def test_record_new_items_is_idempotent(data_root):
    p = make()
    run = run_pipeline(p["id"], trigger="manual", actor="admin", executor=fake_executor(data_root, []))
    with db() as conn:
        n = record_new_items(conn, pipeline_id=p["id"], run_id=run["run_id"], records=[rec(1)], source_types={"rss-a": "rss"})
        again = record_new_items(conn, pipeline_id=p["id"], run_id=run["run_id"], records=[rec(1)], source_types={"rss-a": "rss"})
    assert (n, again) == (1, 0)


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
        preview_pipeline({**ROW, "topic": "bad"}, cfg(), resolve=resolve)
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
                     "VALUES ('run-x',%s,1,'schedule','running',%s,%s)", (p["id"], started, started))
        conn.execute("UPDATE collection_pipelines SET last_status='running', last_run_id='run-x' WHERE id=%s", (p["id"],))
    now = datetime(2026, 11, 11, 2, 0, tzinfo=timezone.utc)
    assert recover_interrupted_pipeline_runs(now) == ["run-x"]
    run = list_runs(p["id"])[0]
    assert run["status"] == "failed" and run["error"] == "SERVICE_INTERRUPTED" and run["finished_at"] == now
    after = get_pipeline(p["id"])
    assert after["last_status"] == "failed" and after["next_run_at"] == now + timedelta(seconds=3600)
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
        conn.execute("INSERT INTO collection_pipelines(id,kind,name,topic,enabled,current_version,interval_sec,owner,created_by) "
                     "VALUES ('bi1','builtin','内置采集器','usiran',true,1,3600,'system','system')")
        conn.execute("INSERT INTO collection_pipeline_versions(pipeline_id,version,config,source_channel) "
                     "VALUES ('bi1',1,'{\"schema_version\":\"pipeline/1\",\"builtin\":true}','migration')")


def builtin_executor(status="success", import_status="succeeded", calls=None, error_message=None):
    def run(**kw):
        if calls is not None:
            calls.append(kw)
        return {"status": status, "snapshot_id": "snap-b" if import_status == "succeeded" else None,
                "import_status": import_status, "error_message": error_message, "results": []}
    return run


def test_run_builtin_success_writes_run_without_items():
    _builtin()
    calls = []
    run = pipeline_runs.run_builtin("bi1", trigger="schedule", actor="scheduler",
                                    executor=builtin_executor(calls=calls))
    assert run["status"] == "success" and run["snapshot_id"] is None and run["trigger"] == "schedule"
    assert run["stats"]["import_status"] == "deferred"
    kw = calls[0]
    assert kw["ids"] == ["bi1"] and kw["auto_import"] is False
    assert kw["run_id_override"] == run["run_id"]
    after = get_pipeline("bi1")
    assert after["last_status"] == "success" and after["last_new_items"] == 0 and after["last_run_id"] == run["run_id"]
    assert count("collection_pipeline_run_items") == 0


def test_run_builtin_failed_and_partial():
    _builtin()
    failed = pipeline_runs.run_builtin("bi1", trigger="manual", actor="a",
                                       executor=builtin_executor("failed", "skipped_collection_status", error_message="炸了"))
    assert failed["status"] == "failed" and "炸了" in failed["error"]
    partial = pipeline_runs.run_builtin("bi1", trigger="manual", actor="a", executor=builtin_executor("partial"))
    assert partial["status"] == "partial"


def test_run_builtin_busy_discards_row():
    _builtin()

    def busy(**kw):
        raise CollectorBusyError("lock is held")

    with pytest.raises(CollectorBusyError):
        pipeline_runs.run_builtin("bi1", trigger="schedule", actor="scheduler", executor=busy)
    assert count("collection_pipeline_runs") == 0 and get_pipeline("bi1")["last_run_id"] is None


def test_run_builtin_rejects_configurable():
    p = make()
    with pytest.raises(PipelineError):
        pipeline_runs.run_builtin(p["id"], trigger="manual", actor="a", executor=builtin_executor())


def test_run_builtin_unpublished_output_is_failed_and_not_backfilled():
    _builtin()

    def gated(**kw):
        return {"status": "success", "import_status": "not_requested",
                "results": [{"id": "bi1", "returncode": 0, "published": False}]}

    run = pipeline_runs.run_builtin("bi1", trigger="schedule", actor="scheduler", executor=gated)
    assert run["status"] == "failed" and "发布门禁" in run["error"]
    assert pipeline_runs.backfill_merged_runs("s1", imported_before=datetime.now(timezone.utc)) == 0
    assert list_runs("bi1")[0]["snapshot_id"] is None
