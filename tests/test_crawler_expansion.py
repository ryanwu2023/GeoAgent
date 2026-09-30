import json
import pytest
from backend.adapters import load_bundle
from backend.research import metric_view,filtered_records,analysis

def test_multiple_crawler_dimensions_survive_dedup(tmp_path,monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path/'data'))
    for monitor in ('israel-monitor','diplomacy-monitor'):
        folder=tmp_path/monitor/'output';folder.mkdir(parents=True)
        (folder/'ALL-records.jsonl').write_text(json.dumps({'title':'Iran ceasefire talks','url':'https://example.org/one','buckets':['talks']}),encoding='utf-8')
    bundle=load_bundle(tmp_path)
    assert len(bundle['records'])==1
    assert len(filtered_records(bundle,'usiran','israel'))==1
    assert len(filtered_records(bundle,'usiran','diplomacy'))==1

def test_yield_basis_points_and_shipping_step():
    base={'unit':'%','bucket':'rates','history':[{'date':'2026-09-17','value':4.94},{'date':'2026-09-18','value':5.01}]}
    view=metric_view(base)
    assert view['delta']==pytest.approx(7)
    assert view['delta_unit']=='基点' and view['pct'] is None
    ship={'unit':'艘次','comparison_step':7,'history':[{'date':f'2026-09-{i:02}','value':i} for i in range(1,9)]}
    assert metric_view(ship)['previous']['date']=='2026-09-01'
    assert metric_view(ship)['delta']==7

def test_finance_archive_shared_market_and_missing(tmp_path,monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path/'data'))
    folder=tmp_path/'finance-front-monitor'/'output';folder.mkdir(parents=True)
    row={'series':'dgs10','label':'10年美债收益率','unit':'%','cadence':'daily','bucket':'rates','date':'2026-09-18','value':5.01,'window':[['2026-09-17',4.94],['2026-09-18',5.01]],'run_tag':'2026-09-19T01:08','step':1,'new_point':False}
    (folder/'LATEST-records.jsonl').write_text(json.dumps(row),encoding='utf-8')
    bundle=load_bundle(tmp_path)
    bundle['created_at']='2026-09-19T01:08:00+00:00'
    assert bundle['records'][0]['raw_refs']
    assert bundle['observations'][0]['shared_market']
    rates=next(a for a in analysis(bundle,'ukraine')['assets'] if a['id']=='rates')
    assert '+7.00基点' in rates['market']
    assert rates['direction']=='债价承压'
    assert '未作冲突因果归因' in rates['assessment_kind']
    assert rates['evidence_ids']==[bundle['records'][0]['id']]
    row.update(series='brent_vol20',bucket='oil',label='布伦特波动率',value=4600)
    (folder/'LATEST-records.jsonl').write_text(json.dumps(row),encoding='utf-8')
    quarantined=load_bundle(tmp_path)['observations'][0]
    assert quarantined['history']==[] and quarantined['quality_issue']
