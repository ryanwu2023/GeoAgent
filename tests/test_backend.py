import asyncio
import json
from copy import deepcopy
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.adapters import load_bundle, normalize, safe_url, classify, clean_record_topics
from backend.store import save_snapshot, get_snapshot, snapshots, db, set_working_snapshot_id
from backend.research import metric_view, answer, analysis, create_brief, retrieve, candidates
from backend.app import app

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

def bundle():
    r=normalize({"id":"one", "title":"Iran military supply report", "summary":"Iran supply is discussed; position is not confirmed.", "link":"https://example.org/news/1", "published":"2026-09-17T08:00:00Z", "source_id":"official", "tier":"T1", "party":"iran"}, "readiness-monitor")
    return {"records":[r], "observations":[{"id":"spend","name":"采购","unit":"USD","scope":"美国整体背景","method":"net outlay","frequency":"月度","topic":None,"dimension":"us_readiness","source_ids":[r["id"]],"history":[{"date":"2026-07-31","value":100},{"date":"2026-08-31","value":110}]}], "sources":[], "manifest":[], "errors":[]}

def stored():
    sid,_=save_snapshot(bundle())
    return get_snapshot(sid)

def test_snapshot_idempotency_revision_and_immutability():
    data=bundle();first,created=save_snapshot(data)
    assert created
    assert save_snapshot(data)==(first,False)
    revised=deepcopy(data);revised["records"][0]["text"]="Corrected text"
    second,_=save_snapshot(revised)
    assert first!=second
    assert get_snapshot(first)["records"][0]["text"]!=get_snapshot(second)["records"][0]["text"]
    assert get_snapshot(second)["import_stats"]=={"new":0,"revised":1,"total":1}
    assert len(snapshots())==2

def test_metric_missing_is_not_zero_and_zero_baseline():
    m=bundle()["observations"][0]
    assert metric_view(m)["pct"]==10
    m["history"][0]["value"]=0
    assert metric_view(m)["pct"] is None
    assert metric_view(m)["delta"]==110
    m["history"][1]["value"]=None
    assert metric_view(m)["delta"] is None
    assert metric_view(m)["current"]["value"] is None

def test_mentions_and_source_tier_do_not_establish_deployment():
    r=normalize({"title":"Navy housing", "tier":"T1", "places":[{"name":"Newport News","lat":37,"lon":-76}],"localized":[{"ships":["USS X"],"ambiguous":False}]},"naval-monitor")
    assert r["verification"]=="待核验"
    assert r["points"][0]["precision"]=="mention"
    assert "非部署" in r["points"][0]["role"]

def test_topic_relevance_cleaning_quarantines_noise_but_keeps_raw_record():
    cases = [
        ("ukraine", "俄罗斯堪察加半岛发生6.9级地震", "自然灾害资讯"),
        ("ukraine", "俄罗斯报告新增猴痘病例", "公共卫生资讯"),
        ("ukraine", "EU calls for measures amid trade deficit with China", "与俄乌无直接关联的一般贸易资讯"),
        ("usiran", "Iran wins U23 wrestling world championship", "体育资讯"),
        ("usiran", "Jafari nominated for AFC Awards Bangkok 2026", "体育资讯"),
    ]
    for index, (topic, title, reason) in enumerate(cases):
        record = normalize({"title": title, "summary": "", "link": f"https://example.org/noise/{index}"}, "new")
        record["topics"] = [topic]
        record["dimensions"] = {topic: "frontline" if topic == "ukraine" else "iran_readiness"}
        record["dimension_candidates"] = {topic: list(record["dimensions"].values())}
        removed = clean_record_topics(record)
        assert removed[0]["reason"] == reason
        assert record["topics"] == []
        assert record["classification"] == "相关性清洗排除"
        assert record["title"] == title

def test_topic_relevance_cleaning_preserves_conflict_and_asset_transmission_news():
    titles = [
        "Iran nuclear talks resume despite World Cup security concerns",
        "Russia-Ukraine energy ceasefire lifts European markets after EU-China deal",
        "Russian budget deficit widens as sanctions and war spending increase",
    ]
    for index, title in enumerate(titles):
        record = normalize({"title": title, "summary": "", "link": f"https://example.org/relevant/{index}"}, "new")
        record["topics"] = ["ukraine" if "Russia" in title else "usiran"]
        assert clean_record_topics(record) == []

def test_ukraine_publisher_suffix_not_topic_anchor():
    topics,_=classify({"title":"Iran oil | Ukraine news - #Mezha", "summary":"Hormuz report"})
    assert topics==["usiran"]

def test_url_safety_and_tracking_deduplication():
    assert safe_url("javascript:alert(1)")==""
    assert safe_url("https://example.org/a?utm_source=x&api_key=secret")=="https://example.org/a"
    assert safe_url("https://user:secret@example.org")==""

def test_crawler_dedup_preserves_archives_and_partial_failures(tmp_path):
    root=tmp_path/"crawlers"
    raw={"id":"one","link":"https://example.org/shared","title":"Iran report","summary":"same source"}
    for name in ["naval-monitor","readiness-monitor"]:
        d=root/name/"output";d.mkdir(parents=True)
        (d/"ALL-records.jsonl").write_text(json.dumps(raw)+"\n{bad\n",encoding="utf-8")
    s=load_bundle(root)
    assert len(s["records"])==1
    assert len(s["records"][0]["adapters"])==2
    assert len(s["errors"])==2
    assert s["cleaning"]=={"raw_records":4,"duplicates_merged":1,"invalid_records":2,"unique_records":1}
    assert any(x["state"]=="not_connected" for x in s["sources"])

def test_extension_contract_enters_topic_metrics_and_evidence(tmp_path):
    d=tmp_path/"new-crawler"/"output";d.mkdir(parents=True)
    data={"schema_version":"geo.v6/1","records":[{"id":"a","title":"Aid delivery","summary":"Actual public record","link":"https://example.org/aid","topics":["ukraine"],"dimensions":{"ukraine":"aid"}}],"observations":[{"id":"aid","name":"援助交付","unit":"USD","frequency":"月度","scope":"指定项目","topic":"ukraine","dimension":"aid","method":"actual delivery","history":[{"date":"2026-09-01","value":10}],"source_ids":["a"]}]}
    (d/"geo-v6.json").write_text(json.dumps(data),encoding="utf-8")
    (d/"_source_status.json").write_text(json.dumps({"run_at":"2026-10-18T00:00:00Z","sources":[{"id":"official-api","publisher":"官方接口","state":"configuration_required","error":"缺少测试凭据"}]}),encoding="utf-8")
    s=load_bundle(tmp_path)
    assert s["records"][0]["dimensions"]=={"ukraine":"aid"}
    assert s["observations"][0]["source_ids"]==[s["records"][0]["id"]]
    assert next(source for source in s["sources"] if source["monitor"]=="new-crawler")["state"]=="configuration_required"
    assert s["projects"][0]["failed_sources"]==1
    data["observations"][0]["source_ids"]=["missing"]
    (d/"geo-v6.json").write_text(json.dumps(data),encoding="utf-8")
    with pytest.raises(ValueError):load_bundle(tmp_path)

def test_api_filters_and_snapshot_bound_citations(tmp_path, monkeypatch):
    s=stored();client=TestClient(app)
    monkeypatch.setattr("backend.app.collector_data_root", lambda: tmp_path / "collector-output")
    collector_summary=client.get('/api/collector/summary')
    assert collector_summary.status_code==200
    assert collector_summary.json()['available'] is False
    collector_runs=client.get('/api/collector/runs')
    assert collector_runs.status_code==200
    assert collector_runs.json()['runs']==[]
    snapshot_projection=client.get('/api/collector/snapshots')
    assert snapshot_projection.status_code==200
    assert snapshot_projection.json()['source']=='research-sqlite-snapshot-projection'
    assert snapshot_projection.json()['snapshots'][0]['id']==s['id']
    assert snapshot_projection.json()['snapshots'][0]['records']==1
    assert client.get('/api/workspace-selection').json()['available'] is False
    selected=client.put('/api/workspace-selection',json={'snapshot_id':s['id']})
    assert selected.status_code==200 and selected.json()['snapshot_id']==s['id']
    assert client.get('/api/workspace-selection').json()['snapshot_id']==s['id']
    assert client.get('/api/status').json()['working_snapshot_id']==s['id']
    assert client.put('/api/workspace-selection',json={'snapshot_id':'missing'}).status_code==404
    assert client.get('/api/collector/ledger').json()=={"available":False,"source":"none","runs":[]}
    assert client.get('/api/collector/sources').json()=={"available":False,"source":"none","collector_id":None,"collectors":[]}
    assert client.get('/api/collector/runs/missing/events').json()["available"] is False
    assert client.get('/api/collector/runs/missing').status_code==404
    assert len(client.get("/api/workspace",params={"snapshot_id":s["id"],"topic":"ukraine"}).json()["records"])==0
    assert len(client.get("/api/workspace",params={"snapshot_id":s["id"],"start":"2026-09-18"}).json()["records"])==0
    assert client.get("/api/workspace",params={"snapshot_id":"missing"}).status_code==404
    assert client.get("/api/workspace",params={"snapshot_id":s["id"],"start":"2026-09-20","end":"2026-09-01"}).status_code==422
    light_workspace=client.get("/api/workspace",params={"snapshot_id":s["id"],"include_records":False}).json()
    assert light_workspace["records"]==[] and light_workspace["metrics"]
    mapped=client.get("/api/map-records",params={"snapshot_id":s["id"],"topic":"usiran"}).json()
    assert mapped and mapped[0]["id"]==s["records"][0]["id"] and mapped[0]["points"]
    assert client.post("/api/import",headers={"Origin":"https://untrusted.test"}).status_code==403
    page=client.get("/api/evidence",params={"snapshot_id":s["id"],"q":"Iran","limit":1}).json()
    assert page["total"]==1 and len(page["records"])==1 and page["has_more"] is False
    assert client.get("/api/evidence",params={"snapshot_id":s["id"],"q":"missing"}).json()["records"]==[]
    assert "text" not in client.get("/api/workspace",params={"snapshot_id":s["id"]}).json()["records"][0]
    result=client.post("/api/ask",json={"snapshot_id":s["id"],"question":"采购指标变化","topic":"usiran","use_model":False}).json()
    assert result["mode"]=="本地证据检索"
    assert "10.00%" in result["facts"][0]["text"]
    assert result["snapshot_id"]==s["id"]
    assert all(r["id"] in {x["id"] for x in s["records"]} for r in result["citations"])
    assert "因果验证" in " ".join(result["gaps"])


def test_collector_run_actions_validate_and_queue(monkeypatch):
    client = TestClient(app)
    captured = []

    class Job:
        def public(self):
            return {"request_id": "req-1", "run_id": "manual-1", "status": "queued"}

    def submit(**kwargs):
        captured.append(kwargs)
        return Job()

    monkeypatch.setattr("backend.app.collector_jobs.submit", submit)
    response = client.post(
        "/api/collector/runs",
        json={"ids": ["xinhua-monitor"], "retry_attempts": 2},
        headers={"Origin": "http://127.0.0.1:8010"},
    )
    assert response.status_code == 202
    assert response.json()["run_id"] == "manual-1"
    assert captured[0]["ids"] == ["xinhua-monitor"]
    assert client.post("/api/collector/runs", json={"ids": ["does-not-exist"]}).status_code == 422


def test_manual_run_inherits_partial_import_policy_from_project_config(monkeypatch):
    client = TestClient(app)
    captured = []

    class Job:
        def public(self):
            return {"request_id": "req-config", "run_id": "manual-config", "status": "queued"}

    monkeypatch.setattr("backend.app.load_config", lambda: {"allow_partial_import": True})
    monkeypatch.setattr("backend.app.collector_jobs.submit", lambda **kwargs: captured.append(kwargs) or Job())
    response = client.post("/api/collector/runs", json={"ids": ["xinhua-monitor"]})
    assert response.status_code == 202
    assert captured[0]["allow_partial_import"] is True


def test_collector_retry_action_only_queues_failed_items(tmp_path, monkeypatch):
    client = TestClient(app)
    run_dir = tmp_path / "data" / "collection-runs" / "run-failed"
    run_dir.mkdir(parents=True)
    (run_dir / "results.json").write_text(
        json.dumps(
            {
                "run_kind": "scheduled-service",
                "status": "partial",
                "results": [
                    {"id": "xinhua-monitor", "returncode": "timeout", "started_at": "2026-10-02T09:00:00+00:00"},
                    {"id": "diplomacy-monitor", "returncode": 0, "started_at": "2026-10-02T09:00:00+00:00"},
                ],
            }
        ),
        encoding="utf-8",
    )

    class Job:
        def public(self):
            return {"request_id": "req-2", "run_id": "manual-2", "status": "queued"}

    captured = []
    monkeypatch.setattr("backend.app.collector_jobs.submit", lambda **kwargs: captured.append(kwargs) or Job())
    response = client.post("/api/collector/runs/run-failed/retry")
    assert response.status_code == 202
    assert captured == [{"ids": ["xinhua-monitor"], "auto_import": True, "allow_partial_import": False, "exclude_ids": []}]


def test_manual_run_rejects_explicitly_excluded_collector(monkeypatch):
    monkeypatch.setattr(
        "backend.app.load_config",
        lambda: {"exclude_ids": ["taco-monitor"], "allow_partial_import": False},
    )
    client = TestClient(app)
    response = client.post("/api/collector/runs", json={"ids": ["taco-monitor"]})
    assert response.status_code == 409
    assert "排除" in response.text


def test_collector_review_gate_and_partial_import_require_explicit_approval(tmp_path, monkeypatch):
    client = TestClient(app)
    run_dir = tmp_path / "data" / "collection-runs" / "run-review"
    run_dir.mkdir(parents=True)
    report = {
        "run_id": "run-review",
        "run_kind": "manual",
        "status": "partial",
        "started_at": "2026-10-02T09:00:00+00:00",
        "finished_at": "2026-10-02T09:01:00+00:00",
        "import_status": "skipped_output_gate",
        "results": [{"id": "demo", "returncode": "timeout"}],
    }
    (run_dir / "results.json").write_text(json.dumps(report), encoding="utf-8")

    review = client.get("/api/collector/runs/run-review/review")
    assert review.status_code == 200
    assert {item["item_id"] for item in review.json()["items"]} == {"sources", "candidates", "gate", "import", "snapshot"}
    assert client.post("/api/collector/runs/run-review/import-retry", json={"allow_partial_import": True}).status_code == 409

    approved = client.post("/api/collector/runs/run-review/gate", json={"decision": "approved", "reviewer": "tester"})
    assert approved.status_code == 200
    assert approved.json()["review"]["status"] == "accepted"
    monkeypatch.setattr("backend.app.import_collector_output", lambda: {"snapshot_id": "snapshot-1", "created": True, "records": 2, "observations": 1, "errors": 0})
    imported = client.post("/api/collector/runs/run-review/import-retry", json={"allow_partial_import": True})
    assert imported.status_code == 200
    assert imported.json()["snapshot_id"] == "snapshot-1"
    assert client.get("/api/collector/runs/run-review/review").json()["items"][2]["status"] == "accepted"


def test_analysis_endpoints_require_confirmed_working_snapshot():
    client = TestClient(app)
    sid, _ = save_snapshot(bundle())
    assert client.post("/api/ask", json={"snapshot_id": sid, "question": "采购指标变化"}).status_code == 409
    assert client.post("/api/briefs", json={"snapshot_id": sid}).status_code == 409


def test_collector_log_tail_reads_and_redacts_report_output(tmp_path, monkeypatch):
    run_dir = tmp_path / "data" / "collection-runs" / "run-logs"
    run_dir.mkdir(parents=True)
    (run_dir / "results.json").write_text(
        json.dumps({"results": [{"id": "demo", "stderr_tail": "token=secret-value\nparse failed"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.app.data_dir", lambda: tmp_path / "data")
    client = TestClient(app)

    response = client.get("/api/collector/runs/run-logs/logs/demo?stream=stderr&tail=200")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert "secret-value" not in payload["content"]
    assert "[REDACTED]" in payload["content"]
    assert client.get("/api/collector/runs/run-logs/logs/demo?stream=bad").status_code == 422

def test_no_evidence_does_not_invent_answer():
    result=asyncio.run(answer(stored(),"火星土壤",topic="ukraine"))
    assert result["citations"]==[] and result["facts"]==[]
    assert result["summary"] is None
    assert "没有足够证据" in result["message"]

def fake_model(monkeypatch, payload=None, error=False):
    monkeypatch.setenv("LLM_API_KEY","unit-test-not-a-real-key")
    monkeypatch.setenv("LLM_MODEL","mock")
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kw):
            assert kw["json"]["messages"][0]["role"]=="system"
            if error:raise httpx.ReadTimeout("simulated")
            body=dict(payload or {})
            if "summary" not in body:
                context=json.loads(kw["json"]["messages"][1]["content"])["context"]
                body["summary"]={"text":"根据当前材料，局势存在新的进展，但相关陈述仍需交叉核验，后续走向具有不确定性。","evidence_ids":[context["evidence"][0]["id"]]}
            return httpx.Response(200,request=httpx.Request("POST","https://example.org"),json={"choices":[{"message":{"content":json.dumps(body)}}]})
    monkeypatch.setattr("backend.research.httpx.AsyncClient",Client)

def test_model_rejects_invented_citations_and_falls_back(monkeypatch):
    fake_model(monkeypatch,{"inferences":[{"text":"有影响","evidence_ids":["invented"]}]})
    r=asyncio.run(answer(stored(),"Iran supply"))
    assert "模型失败" in r["mode"] and r["citations"]
    assert r["inferences"]==[]

def test_model_timeout_retains_evidence(monkeypatch):
    fake_model(monkeypatch,error=True)
    r=asyncio.run(answer(stored(),"Iran supply"))
    assert "模型失败" in r["mode"] and r["citations"]

def test_valid_model_assets_are_versioned_and_scoped(monkeypatch):
    s=stored();rid=s["records"][0]["id"]
    payload={"inferences":[{"text":"材料仅提供供给线索，需要核验持续性。","evidence_ids":[rid]}],"assets":[{"id":"commodity","direction":"双向作用","mechanism":"供需作用需要联合核验","condition":"确认实际供给变化","counter":"替代供给抵消","watch":"库存变化","evidence_ids":[rid]}]}
    fake_model(monkeypatch,payload)
    r=asyncio.run(answer(s,"Iran supply 资产影响",topic="usiran"))
    assert r["mode"]=="现场模型分析"
    assert r["summary"]["text"].startswith("根据当前材料")
    assert analysis(s,"usiran")["assets"][3]["direction"]=="双向作用"
    assert analysis(s,"ukraine")["assets"][3]["direction"]=="证据不足"

def test_model_new_numbers_rejected(monkeypatch):
    s=stored();rid=s["records"][0]["id"]
    fake_model(monkeypatch,{"inferences":[{"text":"上涨50%","evidence_ids":[rid]}]})
    assert "模型失败" in asyncio.run(answer(s,"Iran supply"))["mode"]

def test_model_summary_new_numbers_rejected(monkeypatch):
    s=stored();rid=s["records"][0]["id"]
    fake_model(monkeypatch,{"summary":{"text":"综合材料判断，局势升级概率达到99%，但仍需核验。","evidence_ids":[rid]},"inferences":[]})
    result=asyncio.run(answer(s,"Iran supply"))
    assert "模型失败" in result["mode"] and result["summary"] is None

def test_model_summary_ignores_structured_evidence_id_digits(monkeypatch):
    s=stored();rid=s["records"][0]["id"]
    text=f"根据当前材料，局势存在新的进展，但仍需交叉核验（证据 {rid}）。"
    fake_model(monkeypatch,{"summary":{"text":text,"evidence_ids":[rid]},"inferences":[]})
    result=asyncio.run(answer(s,"Iran supply"))
    assert result["mode"]=="现场模型分析"
    assert rid not in result["summary"]["text"] and "根据当前材料" in result["summary"]["text"]

def test_model_allows_numbers_present_in_cited_evidence(monkeypatch):
    data=bundle();data["records"][0]["text"] += " Public reporting states 50% of the route reopened on 2026-09-16."
    sid,_=save_snapshot(data);s=get_snapshot(sid);rid=s["records"][0]["id"]
    fake_model(monkeypatch,{"inferences":[{"text":"材料称2026年9月16日已有50%的航线恢复，但仍需核验。","evidence_ids":[rid]}]})
    result=asyncio.run(answer(s,"Iran supply"))
    assert result["mode"]=="现场模型分析"

def test_model_allows_dates_from_cited_evidence_metadata(monkeypatch):
    s=stored();rid=s["records"][0]["id"]
    fake_model(monkeypatch,{"inferences":[{"text":"该材料发布于2026年9月17日，结论仍需核验。","evidence_ids":[rid]}]})
    result=asyncio.run(answer(s,"Iran supply"))
    assert result["mode"]=="现场模型分析"

def test_general_model_question_does_not_send_full_rule_histories(monkeypatch):
    s=stored();rid=s["records"][0]["id"]
    async def compact_complete(messages):
        user=json.loads(messages[1]["content"])
        assert len(messages[1]["content"]) < 20_000
        assert user["context"]["rules"] == {"rule_version": "v6.3"}
        return json.dumps({"summary":{"text":"根据当前材料，近期局势仍在变化，现有信息需要继续核验后再形成方向判断。","evidence_ids":[rid]},"inferences":[{"text":"材料显示局势仍需持续核验。","evidence_ids":[rid]}]}),"mock"
    monkeypatch.setattr("backend.research.llm.complete",compact_complete)
    monkeypatch.setenv("LLM_API_KEY","unit-test-not-a-real-key")
    result=asyncio.run(answer(s,"近期伊朗局势有什么进展",topic="usiran"))
    assert result["mode"]=="现场模型分析"
    assert result["summary"]["evidence_ids"]==[rid]

def test_three_rehearsals_brief_download_and_comparison():
    client=TestClient(app);s=stored();set_working_snapshot_id(s["id"]);ids=[]
    for _ in range(3):
        for topic in ["usiran","ukraine"]:
            w=client.get("/api/workspace",params={"snapshot_id":s["id"],"topic":topic})
            assert w.status_code==200
            assert len(w.json()["assets"])==5
            a=client.post("/api/ask",json={"snapshot_id":s["id"],"topic":topic,"question":"当前指标变化与缺口","use_model":False})
            assert a.status_code==200
        b=client.post("/api/briefs",json={"snapshot_id":s["id"],"use_model":False})
        assert b.status_code==200
        ids.append(b.json()["id"])
        md=client.get(f'/api/briefs/{ids[-1]}/download')
        assert "美伊局势" in md.text and "俄乌冲突" in md.text and "反证" in md.text
        assert "## 核心判断" in md.text and "### 俄乌冲突" in md.text
        assert "战线和远程打击变化、援助实际交付与停火执行" in md.text
        assert "## 大类资产影响" in md.text and "## 证据与信源" in md.text
    assert client.get("/api/brief-diff",params={"before":ids[0],"after":ids[1]}).status_code==200
    brief_rows = client.get("/api/briefs").json()
    assert len(brief_rows)==3
    assert "markdown" not in brief_rows[0]
    detail = client.get(f'/api/briefs/{ids[-1]}')
    assert detail.status_code == 200 and detail.json()["markdown"]

def test_known_reprints_count_as_one_retrieval_origin():
    original = normalize({"title":"Iran oil report","link":"https://example.org/original"}, "one")
    reprint = normalize({"title":"Iran oil report copied","link":"https://example.org/copy","original_url":"https://example.org/original"}, "two")
    assert original["origin_id"] == reprint["origin_id"]
    assert len(retrieve([original,reprint], "Iran oil")) == 1
    assert len(candidates([original,reprint], ["oil"])) == 1

def test_ask_invalid_range_and_calendar_dates_rejected():
    s=stored();client=TestClient(app)
    for start,end in [("2026-02-30",""),("2026-09-20","2026-09-01")]:
        assert client.post("/api/ask",json={"snapshot_id":s["id"],"question":"指标变化","start":start,"end":end}).status_code==422

def test_this_week_uses_snapshot_date_and_no_stale_monthly_metrics():
    s=stored();s["created_at"]="2026-09-18T08:00:00+00:00"
    result=asyncio.run(answer(s,"本周哪些指标变化？"))
    assert result["filters"] == {"dimension":"","start":"2026-09-14","end":"2026-09-18"}
    assert result["facts"] == []


def test_research_review_history_conflict_and_snapshot_isolation():
    s=stored();sid=s['id'];rid=s['records'][0]['id'];client=TestClient(app)
    path=f'/api/reviews/{rid}?snapshot_id={sid}'
    original=client.get(f'/api/evidence/{rid}?snapshot_id={sid}').json()
    body={'signal':'escalation','reviewer':'研究员','reason':'报道出现新增行动','version':0}
    saved=client.post(path,json=body)
    assert saved.status_code==200
    assert saved.json()['situation_signal']=='escalation'
    assert saved.json()['verification']==original['verification']
    assert client.post(path,json=body).status_code==409
    body.update(version=1,signal='easing',reason='复核后调整')
    assert client.post(path,json=body).status_code==200
    assert [r['signal'] for r in client.get(path).json()]==['easing','escalation']
    assert client.post(path,json={**body,'reason':' '}).status_code==422
    assert client.post(path.replace(rid,'missing'),json=body).status_code==404
    changed=bundle();changed['records'][0]['text']='new';sid2,_=save_snapshot(changed)
    assert client.get(f'/api/reviews/{rid}?snapshot_id={sid2}').json()==[]
    assert client.get('/api/workspace',params={'snapshot_id':sid,'topic':'all'}).json()['records'][0]['situation_signal']=='easing'


def test_map_cluster_titles_are_batch_translated_and_cached(monkeypatch):
    s = stored()
    record = s['records'][0]

    async def fake_complete(messages):
        request = json.loads(messages[-1]['content'])
        assert request['items'] == [{'id': record['id'], 'title': record['title']}]
        return json.dumps({'items': [{'id': record['id'], 'title': '伊朗军事补给报告'}]}, ensure_ascii=False), 'test-model'

    monkeypatch.setattr('backend.app.llm.complete', fake_complete)
    client = TestClient(app)
    response = client.post('/api/translations/titles', json={
        'snapshot_id': s['id'],
        'record_ids': [record['id']],
    })
    assert response.status_code == 200
    assert response.json()['titles'] == {record['id']: '伊朗军事补给报告'}
    shown = client.get('/api/workspace', params={'snapshot_id': s['id'], 'topic': 'all'}).json()
    assert shown['records'][0]['title'] == '伊朗军事补给报告'


def test_map_title_translation_rejects_records_outside_snapshot():
    s = stored()
    response = TestClient(app).post('/api/translations/titles', json={
        'snapshot_id': s['id'],
        'record_ids': ['missing-record'],
    })
    assert response.status_code == 404


def test_takeaway_uses_recent_sources_and_excludes_future():
    from backend.brief_summary import topic_takeaway
    s=stored();s['created_at']='2026-09-21T01:00:00+00:00'
    r=s['records'][0];r.update(title='伊朗称谈判仍在进行',text='谈判',published_at='2026-09-20T20:00:00+00:00',topics=['usiran'])
    future={**r,'id':'future','title':'伊朗宣布未来停火','published_at':'2026-09-22T00:00:00+00:00'}
    s['records'].append(future)
    result=topic_takeaway(s,'usiran',{})
    assert result['evidence_ids']==[r['id']]
    assert '后续走向取决于' in result['text'] and '资产配置上' in result['text']
    assert '未来停火' not in result['text']
    s['records']=[]
    result=topic_takeaway(s,'ukraine',{})
    assert result['evidence_ids']==[] and '暂不能可靠概括' in result['text']

def test_topic_metrics_are_distinct_but_keep_relevant_overlap():
    from backend.research import metrics
    s={'created_at':'2026-09-21T00:00:00+00:00','observations':[]}
    for series in ('hormuz_total','ttf_gas','brent','djia'):
        s['observations'].append({'id':series,'series':series,'name':series,'unit':'美元','frequency':'日频','scope':'市场','method':'测试','boundary':'','source_ids':['x'],'history':[{'date':'2026-09-20','value':1}],'shared_market':True,'dimension':'financial'})
    s['observations'].append({'id':'taco:INDU','series':'INDU','name':'道琼斯工业指数','unit':'点','frequency':'日频','scope':'市场','method':'TACO 输入','boundary':'','source_ids':['taco'],'history':[{'date':'2026-09-20','value':1}],'topic':'usiran','dimension':'financial'})
    s['observations'].append({'id':'shared-poll','series':'taco_trump_approval','name':'特朗普支持率','unit':'%','frequency':'日频','scope':'市场','method':'共享源','boundary':'','source_ids':['shared'],'history':[{'date':'2026-09-20','value':40}],'shared_market':True,'dimension':'financial'})
    s['observations'].append({'id':'taco-poll','series':'RCPPTAPP_approve','name':'特朗普支持率','unit':'%','frequency':'日频','scope':'市场','method':'TACO 输入','boundary':'','source_ids':['taco'],'history':[{'date':'2026-09-20','value':40}],'topic':'usiran','dimension':'financial'})
    assert {m['series'] for m in metrics(s,'usiran')}=={'hormuz_total','brent','INDU','RCPPTAPP_approve'}
    assert {m['series'] for m in metrics(s,'ukraine')}=={'ttf_gas','brent','djia'}
    assert len([m for m in metrics(s,'all') if m['series'] in {'djia','INDU'}])==1
    assert len([m for m in metrics(s,'all') if m['series'] in {'taco_trump_approval','RCPPTAPP_approve'}])==1

def test_huatai_brief_is_high_trust_supplemental_source(tmp_path):
    root=tmp_path/'crawlers';root.mkdir()
    (root/'华泰中东简报.md').write_text('华泰政策 · 中东简报小结 | 2026.09.20\n\n美国与伊朗局势研究内容。',encoding='utf-8')
    data=load_bundle(root)
    record=next(r for r in data['records'] if r['source_id']=='huatai-middle-east-brief')
    source=next(s for s in data['sources'] if s['id']=='huatai-middle-east-brief')
    assert record['tier']=='T2' and record['supplemental'] is True
    assert record['topics']==['usiran'] and record['published_at']=='2026-09-20'
    assert '仍需回源核验' in record['verification']
    assert source['count']==1 and source['latest']=='2026-09-20'
