from backend.assessment import enrich
import asyncio
from backend.research import answer

def metric(series, delta, value=1):
    return dict(series=series, delta=delta, current={'value':value,'date':'2026-09-18'}, previous={'value':1,'date':'2026-09-17'},name=series,source_ids=['source'])

def assess(kind, observations):
    return enrich([dict(id=kind,direction='证据不足',mechanism='机制',evidence_ids=[])],observations)[0]

def test_yield_and_spread_price_sign_and_flat():
    assert assess('rates',[metric('dgs10',1)])['direction']=='债价承压'
    assert assess('rates',[metric('dgs10',-1)])['direction']=='债价获支撑'
    assert assess('credit',[metric('hy_oas',0),metric('ig_oas',0)])['direction']=='市场变化有限'
    assert assess('credit',[metric('hy_oas',1),metric('ig_oas',-1)])['direction']=='市场变化分化'

def test_missing_quarantined_and_references():
    assert assess('rates',[])['direction']=='证据不足'
    bad={**metric('dgs10',1),'quality_issue':'隔离'}
    assert assess('rates',[bad])['direction']=='证据不足'
    result=assess('fx',[metric('eurusd',1),metric('dxy',-1)])
    assert result['evidence_ids']==['source']
    assert '2026-09-17→2026-09-18' in result['view']
    assert '欧元相对走强' in result['view']

def test_rule_answer_keeps_source_citations(tmp_path, monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path))
    record=dict(id='source',title='市场序列',text='市场背景',topics=[],dimensions={},published_at=None)
    obs=dict(id='rate',series='dgs10',name='十年期收益率',unit='%',bucket='rates',shared_market=True,scope='美国',history=[{'date':'2026-09-17','value':4},{'date':'2026-09-18','value':4.1}],source_ids=['source'])
    snapshot=dict(id='snapshot',created_at='2026-09-19T00:00:00Z',records=[record],observations=[obs])
    result=asyncio.run(answer(snapshot,'资产关联',use_model=False))
    assert result['inferences'][0]['kind']=='规则条件研判，非模型输出'
    assert result['citations'][0]['id']=='source'
