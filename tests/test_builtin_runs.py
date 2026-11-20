import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import builtin_config, builtin_runs, pipeline_routes
from backend.adapters import normalize
from backend.builtin_config import BuiltinConfigError
from backend.paths import collector_source_root
from backend.pipeline_runs import claim_trial, execute_trial, list_run_items
from backend.pipeline_scheduler import sync_builtin_pipelines
from backend.pipeline_service import PipelineError, get_pipeline
from backend.store import db

CID = "mideast-monitor"


def public(host):
    return ["93.184.216.34"]


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "collector-output"
    monkeypatch.setenv("COLLECTOR_DATA_ROOT", str(root))
    return root


def draft(cid=CID):
    return {"sources": [builtin_config.source_view(s) for s in builtin_config.base_config(cid)["sources"]]}


def raw(n, source, *, kind=None):
    row = {"id": f"x{n}", "title": f"标题 {n}", "link": f"https://example.com/{n}", "source_id": source,
           "published_utc": f"2026-11-1{n}T08:00:00+00:00", "buckets": ["gulf_front"]}
    return {**row, "kind": kind} if kind else row


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


def fake_runner(rows, *, fetch=None, returncode=0, calls=None):
    def run(cid, config, work, *, timeout):
        if calls is not None:
            calls.append({"cid": cid, "config": config, "work": work})
        out = Path(work) / "output"
        write_jsonl(out / "ALL-records.jsonl", rows)
        if fetch is not None:
            (out / "_fetch_stats.json").write_text(json.dumps({"src": fetch}), encoding="utf-8")
        return {"returncode": returncode, "output": out, "stdout": "", "stderr": "boom" if returncode else ""}
    return run


def test_read_results_maps_records_excluded_and_fetch_stats(tmp_path):
    out = tmp_path / "output"
    write_jsonl(out / "ALL-records.jsonl", [raw(1, "a"), raw(2, "a", kind="excluded"), raw(1, "a")])
    (out / "_fetch_stats.json").write_text(json.dumps({"src": {"a": {"ok": True, "n": 7}, "b": {"ok": False, "err": "http_403"}}}),
                                           encoding="utf-8")
    found = builtin_runs.read_results(CID, out)
    assert [(i["title"], i["accepted"], i["reason"]) for i in found["items"]] == [
        ("标题 1", True, ""), ("标题 2", False, "已被当前口径排除")]
    assert found["items"][0]["id"] == normalize(raw(1, "a"), CID)["id"]
    assert found["items"][0]["matched_discovery_terms"] == ["gulf_front"]
    assert found["statuses"] == {"a": {"state": "ok", "error": None, "fetched": 7},
                                 "b": {"state": "failed", "error": "http_403", "fetched": None}}


def test_read_results_meeting_signals_and_rejects(tmp_path):
    day = tmp_path / "output" / "2026-11-20"
    write_jsonl(day / "signals.jsonl", [{"title": "峰会", "url": "https://w.example/1", "source_id": "wh"}])
    write_jsonl(day / "rejects.jsonl", [{"title": "评论", "url": "https://w.example/2", "source_id": "wh",
                                         "reason": "opinion", "detail": "评论/分析体裁"}])
    items = builtin_runs.read_results("meeting-calendar-monitor", tmp_path / "output")["items"]
    assert [(i["title"], i["accepted"], i["reason"]) for i in items] == [("峰会", True, ""), ("评论", False, "评论/分析体裁")]


def test_preview_sources_prefers_changed_then_first_enabled():
    d = draft()
    saved = builtin_config.base_config(CID)
    enabled = [s["id"] for s in d["sources"] if s["enabled"]]
    assert builtin_runs.preview_sources(CID, d, saved, None) == enabled[:5]
    d["sources"][3]["url"] = "https://changed.example/rss"
    d["sources"].append({**d["sources"][0], "id": "custom-1", "name": "新来源", "url": "https://n.example/rss"})
    assert builtin_runs.preview_sources(CID, d, saved, None) == [d["sources"][3]["id"], "custom-1"]
    assert builtin_runs.preview_sources(CID, d, saved, ["custom-1", "nope"]) == ["custom-1"]


def test_preview_runs_unsaved_draft_on_chosen_sources_only():
    d = draft()
    target = d["sources"][0]
    target["url"] = "https://changed.example/rss"
    calls = []
    out = builtin_runs.preview(CID, d, None, resolve=public, limit=1,
                               runner=fake_runner([raw(1, target["id"]), raw(2, target["id"], kind="excluded"),
                                                   raw(3, target["id"])], calls=calls))
    config = calls[0]["config"]
    assert [s["id"] for s in config["sources"]] == [target["id"]] and config["sources"][0]["url"] == "https://changed.example/rss"
    assert not calls[0]["work"].exists()  # 临时目录用完即删
    [source] = out["sources"]
    assert source["source_id"] == target["id"] and source["state"] == "ok" and len(source["candidates"]) == 1


def test_preview_rejects_invalid_draft_and_readonly():
    d = draft()
    d["sources"][0]["url"] = "http://10.0.0.1/rss"
    with pytest.raises(BuiltinConfigError) as e:
        builtin_runs.preview(CID, d, None, resolve=lambda h: ["10.0.0.1"], runner=fake_runner([]))
    assert e.value.field == "sources[0].url"
    with pytest.raises(builtin_runs.BuiltinRunError):
        builtin_runs.preview("taco-monitor", {"sources": []}, None, runner=fake_runner([]))


def test_preview_failure_reports_stderr():
    with pytest.raises(builtin_runs.BuiltinRunError) as e:
        builtin_runs.preview(CID, draft(), None, resolve=public, runner=fake_runner([], returncode=2))
    assert e.value.message == "试跑失败：boom"


def test_legacy_collector_runs_in_sandbox_copy(tmp_path):
    """海军监测把输出写在自己的源码目录：测试运行要复制一份再跑，不碰正式目录。"""
    source_output = collector_source_root() / "naval-monitor" / "output"
    before = sorted(source_output.rglob("*")) if source_output.exists() else []
    seen = {}

    def run(command, *, cwd, env, timeout, **kw):
        seen.update(cwd=Path(cwd), env=env)
        write_jsonl(Path(cwd) / "output" / "ALL-records.jsonl", [raw(1, "usni-news")])
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    result = builtin_runs.run_collector("naval-monitor", {"sources": []}, tmp_path / "work", timeout=5, run=run)
    assert seen["cwd"] == tmp_path / "work" / "sandbox"
    assert seen["env"]["GEO_COLLECTOR_CONFIG"] == str(tmp_path / "work" / "state" / "config.override.json")
    assert result["output"] == tmp_path / "work" / "sandbox" / "output"
    assert (sorted(source_output.rglob("*")) if source_output.exists() else []) == before


def _sync():
    sync_builtin_pipelines([{"id": CID, "name_zh": "中东地区监测", "topics": ["usiran"]},
                            {"id": "taco-monitor", "name_zh": "TACO", "topics": ["usiran"]}])


def test_builtin_trial_records_items_new_against_live_output(data_root, monkeypatch):
    _sync()
    write_jsonl(data_root / CID / "output" / "ALL-records.jsonl", [raw(1, "a")])  # 正式产物已有 x1
    import backend.builtin_runs as br
    monkeypatch.setattr(br, "run_collector", fake_runner([raw(1, "a"), raw(2, "a"), raw(3, "b", kind="excluded")],
                                                         fetch={"a": {"ok": True, "n": 3}, "b": {"ok": False, "err": "x"}}))
    claim = claim_trial(CID, actor="admin")
    run = execute_trial(claim, actor="admin")
    assert run["status"] == "partial" and run["snapshot_id"] is None
    assert run["stats"]["records"] == 2 and run["stats"]["excluded"] == 1 and run["stats"]["new_items"] == 1
    [item] = list_run_items(run["run_id"])
    assert item["title"] == "标题 2" and item["evidence_id"] == normalize(raw(2, "a"), CID)["id"]
    assert get_pipeline(CID)["last_trigger"] == "trial"
    with db() as conn:
        assert conn.execute("SELECT count(*) AS n FROM pipeline_seen_items").fetchone()["n"] == 0


def test_builtin_trial_failure_and_readonly(data_root, monkeypatch):
    _sync()
    import backend.builtin_runs as br
    monkeypatch.setattr(br, "run_collector", fake_runner([], returncode=3))
    run = execute_trial(claim_trial(CID, actor="admin"), actor="admin")
    assert run["status"] == "failed" and run["error"] == "采集器退出码 3：boom"
    with pytest.raises(PipelineError) as e:
        claim_trial("taco-monitor", actor="admin")
    assert e.value.code == "INVALID"


def test_builtin_preview_route(monkeypatch):
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "true")
    _sync()
    from backend.app import app
    seen = {}

    def fake(cid, config, version_config, **kw):
        seen.update(cid=cid, kw=kw)
        return {"sources": [], "builtin": True}

    monkeypatch.setattr(builtin_runs, "preview", fake)
    client = TestClient(app, base_url="https://testserver")
    r = client.post(f"/api/pipelines/{CID}/preview", json={"config": draft(), "source_ids": ["a"], "limit": 3})
    assert r.status_code == 200 and seen["kw"]["source_ids"] == ["a"] and seen["kw"]["limit"] == 3

    def busy(*a, **k):
        raise builtin_runs.BuiltinRunError(builtin_runs.PREVIEW_BUSY)
    monkeypatch.setattr(builtin_runs, "preview", busy)
    assert client.post(f"/api/pipelines/{CID}/preview", json={"config": draft()}).status_code == 409
    assert client.post(f"/api/pipelines/{CID}/preview", json={"config": draft(), "source_ids": list("abcdef")}).status_code == 422
    assert pipeline_routes.builtin_runs is builtin_runs
