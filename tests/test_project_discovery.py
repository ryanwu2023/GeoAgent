import json
from backend.adapters import load_bundle,normalize

def test_ukraine_binding_and_excluded():
    row={'title':'Domestic policy','url':'https://example.org/item','buckets':['labor_shortage']}
    r=normalize(row,'russia-domestic-monitor')
    assert r['dimension_candidates']['ukraine']==['russia_domestic']
    assert 'usiran' not in r['topics']
    excluded=normalize({**row,'kind':'excluded','title':'Ukraine mentioned but excluded'},'ua-front-monitor')
    assert excluded['topics']==[]

def test_discovery_metadata_and_archived_documents(tmp_path,monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path/'data'))
    for project in ['ua-front-monitor','future-news-monitor']:
        folder=tmp_path/project/'output';folder.mkdir(parents=True)
        row={'id':'1','title':'Ukraine aid','url':'https://example.org/shared','buckets':['weapons_aid'],'flags':{'delivered':False},'kind':'opinion_statement'}
        (folder/'ALL-records.jsonl').write_text(json.dumps(row),encoding='utf-8')
        (folder/'LATEST.md').write_text('Report',encoding='utf-8')
    bundle=load_bundle(tmp_path)
    assert len(bundle['records'])==1
    assert len(bundle['records'][0]['upstream_extractions'])==2
    assert len(bundle['projects'])==2
    assert all(p['state']=='connected' and p['documents'] for p in bundle['projects'])
    assert load_bundle(tmp_path)==bundle
