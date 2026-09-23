import asyncio
import json
from copy import deepcopy
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.adapters import load_bundle, normalize, safe_url, classify
from backend.store import save_snapshot, get_snapshot, snapshots, db
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
    assert any(x["state"]=="not_connected" for x in s["sources"])

def test_extension_contract_enters_topic_metrics_and_evidence(tmp_path):
    d=tmp_path/"new-crawler"/"output";d.mkdir(parents=True)
    data={"schema_version":"geo.v6/1","records":[{"id":"a","title":"Aid delivery","summary":"Actual public record","link":"https://example.org/aid","topics":["ukraine"],"dimensions":{"ukraine":"aid"}}],"observations":[{"id":"aid","name":"援助交付","unit":"USD","frequency":"月度","scope":"指定项目","topic":"ukraine","dimension":"aid","method":"actual delivery","history":[{"date":"2026-09-01","value":10}],"source_ids":["a"]}]}
    (d/"geo-v6.json").write_text(json.dumps(data),encoding="utf-8")
    s=load_bundle(tmp_path)
    assert s["records"][0]["dimensions"]=={"ukraine":"aid"}
    assert s["observations"][0]["source_ids"]==[s["records"][0]["id"]]
    data["observations"][0]["source_ids"]=["missing"]
    (d/"geo-v6.json").write_text(json.dumps(data),encoding="utf-8")
    with pytest.raises(ValueError):load_bundle(tmp_path)

def test_api_filters_and_snapshot_bound_citations():
    s=stored();client=TestClient(app)
    assert len(client.get("/api/workspace",params={"snapshot_id":s["id"],"topic":"ukraine"}).json()["records"])==0
    assert len(client.get("/api/workspace",params={"snapshot_id":s["id"],"start":"2026-09-18"}).json()["records"])==0
    assert client.get("/api/workspace",params={"snapshot_id":"missing"}).status_code==404
    assert client.get("/api/workspace",params={"snapshot_id":s["id"],"start":"2026-09-20","end":"2026-09-01"}).status_code==422
    assert client.post("/api/import",headers={"Origin":"https://untrusted.test"}).status_code==403
    result=client.post("/api/ask",json={"snapshot_id":s["id"],"question":"采购指标变化","topic":"usiran"}).json()
    assert result["mode"]=="本地证据检索"
    assert "10.00%" in result["facts"][0]["text"]
    assert result["snapshot_id"]==s["id"]
    assert all(r["id"] in {x["id"] for x in s["records"]} for r in result["citations"])
    assert "尚未配置" in " ".join(result["gaps"])

def test_no_evidence_does_not_invent_answer():
    result=asyncio.run(answer(stored(),"火星土壤",topic="ukraine"))
    assert result["citations"]==[] and result["facts"]==[]
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
            return httpx.Response(200,request=httpx.Request("POST","https://example.org"),json={"choices":[{"message":{"content":json.dumps(payload)}}]})
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
    assert analysis(s,"usiran")["assets"][3]["direction"]=="双向作用"
    assert analysis(s,"ukraine")["assets"][3]["direction"]=="证据不足"

def test_model_new_numbers_rejected(monkeypatch):
    s=stored();rid=s["records"][0]["id"]
    fake_model(monkeypatch,{"inferences":[{"text":"上涨50%","evidence_ids":[rid]}]})
    assert "模型失败" in asyncio.run(answer(s,"Iran supply"))["mode"]

def test_three_rehearsals_brief_download_and_comparison():
    client=TestClient(app);s=stored();ids=[]
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
        assert "| 引用 |\n|---|" in md.text
    assert client.get("/api/brief-diff",params={"before":ids[0],"after":ids[1]}).status_code==200
    assert len(client.get("/api/briefs").json())==3

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
