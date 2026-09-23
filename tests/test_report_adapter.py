from backend.adapters import load_bundle
from backend.research import retrieve, candidates, coverage
from backend.events import event_metadata

def test_supplement_is_searchable_not_event(tmp_path,monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path/'data'))
    (tmp_path/'deep-research-report.md').write_text('# 研究报告：截至2026年9月19日\n\n## 能源传导\n伊朗原油研究观点。citeturn1search2\n',encoding='utf-8')
    s=load_bundle(tmp_path)
    found=retrieve(s['records'],'能源传导')
    assert found and found[0]['supplemental']
    assert 'turn1search2' not in found[0]['text']
    assert found[0]['report_as_of']=='2026-09-19'
    assert found[0]['published_at'] is None
    assert event_metadata(found[0])['points']==[]
    assert candidates(s['records'],['原油'])==[]
    assert all(t['count']==0 for t in coverage(s))
    assert found[0]['url'].startswith('/api/archive/')
