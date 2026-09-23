from backend.workbench import freshness, important_metrics, topic_paths
from backend.catalog import ASSETS


def test_freshness_does_not_treat_missing_as_zero():
    assert freshness({'current': None}, '2026-09-20')['freshness'] == '无有效值'
    assert freshness({'current': {'date':'2026-09-01','value':0}}, '2026-09-20')['freshness'] == '数据滞后'
    assert freshness({'current': {'date':'2026-09-21','value':1}}, '2026-09-20')['freshness'] == '日期待核对'
    assert freshness({'frequency':'月度','current': {'date':'2026-08-31','value':1}}, '2026-09-20')['freshness'] == '可用'


def test_priority_diversifies_metrics():
    rows=[{'name':str(i),'series':str(i),'bucket':'fiscal','current':{'value':i}} for i in range(8)]
    rows += [{'name':'原油','series':'brent','bucket':'commodity','current':{'value':80}}]
    picked=important_metrics(rows,'usiran')
    assert picked[0]['series']=='brent'
    assert len(picked)==3


def test_paths_keep_topics_and_supplemental_separate():
    records=[{'id':i,'title':'oil energy','text':'oil','published_at':'2026-09-19','topics':[t],'supplemental':supp} for i,t,supp in [('iran','usiran',False),('ukraine','ukraine',False),('report','usiran',True)]]
    assets=[{**a,'evidence_ids':[]} for a in ASSETS]
    topic_paths(assets,'all',records,'2026-09-20')
    commodity=next(a for a in assets if a['id']=='commodity')
    assert commodity['topic_paths'][0]['evidence_ids']==['iran']
    assert commodity['topic_paths'][1]['evidence_ids']==['ukraine']
    assert commodity['topic_paths'][0]['condition']!=commodity['topic_paths'][1]['condition']
