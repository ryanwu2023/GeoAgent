"""Real in-process MCP protocol, using disposable business data only."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import store
from backend.app import app


@pytest.fixture
def business_client(tmp_path, monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR', str(tmp_path))
    snapshot = {'id': 'snap', 'created_at': '2026-10-03T00:00:00Z', 'records': [
        {'id': 'r', 'title': 'Iranian strike', 'text': 'reported', 'url': 'https://example.com/news',
         'source_name': 'Example', 'published_at': '2026-10-02', 'fetched_at': '2026-10-03',
         'topics': ['usiran'], 'dimensions': {'usiran': 'us_readiness'}, 'points': [
             {'label': 'Hormuz', 'lon': 56, 'lat': 26, 'precision': 'area'}]}]}
    monkeypatch.setattr(store, 'get_snapshot', lambda sid: snapshot if sid == 'snap' else (_ for _ in ()).throw(KeyError(sid)))
    monkeypatch.setattr(store, 'get_working_snapshot_id', lambda: 'snap')
    monkeypatch.setattr(store, 'snapshot_summaries', lambda: [{'id': 'snap', 'created_at': snapshot['created_at']}])
    with TestClient(app) as client:
        yield client, tmp_path


def call(client, name, arguments):
    response = client.post('/api/integrations/mcp-workbench/call', json={'tool': name, 'arguments': arguments})
    assert response.status_code == 200
    return json.loads(response.json()['result']['content'][0]['text'])


def test_evidence_link_check_matches_archived_panel_check(business_client):
    client, root = business_client
    audit = root / 'link-audits'
    audit.mkdir()
    (audit / 'snap.jsonl').write_text(json.dumps({'url': 'https://example.com/news', 'status': '可访问',
        'http_status': 200, 'checked_at': '2026-10-02', 'raw_path': '/private/secret'}) + '\n')
    record = call(client, 'geo_evidence_get', {'snapshot_id': 'snap', 'record_id': 'r'})['data']['record']
    assert record['link_health']['status'] == '可访问'
    assert record['link_health']['checked_at'] == '2026-10-02'
    assert 'raw_path' not in record['link_health']
    assert '不等于正文事实已核实' in record['link_health_basis']


def test_map_invalid_filters_and_region_scope_are_explicit(business_client):
    client, _ = business_client
    invalid = call(client, 'geo_risk_map', {'snapshot_id': 'snap', 'categories': ['made-up']})
    assert invalid['ok'] is False
    mapped = call(client, 'geo_risk_map', {'snapshot_id': 'snap', 'topic': 'usiran'})
    assert mapped['ok']
    assert mapped['data']['mapped_records_count'] == 1
    empty = call(client, 'geo_risk_map', {'snapshot_id': 'snap', 'categories': []})
    assert empty['data']['classified_records_count'] == 0


def test_brief_compare_real_archived_text_and_missing_id(business_client):
    client, _ = business_client
    with store.db() as conn:
        conn.execute('INSERT INTO snapshots(id,created_at,payload) VALUES(?,?,?)', ('snap', '2026-10-03', '{}'))
        for bid, content in [('before', '# 观察\n价格上涨\n100 USD'), ('after', '# 观察\n价格回落\n110 USD')]:
            conn.execute('INSERT INTO briefs(id,snapshot_id,created_at,mode,markdown) VALUES(?,?,?,?,?)',
                         (bid, 'snap', '2026-10-03', 'rules', content))
    payload = call(client, 'geo_brief_compare', {'before': 'before', 'after': 'after'})
    assert payload['ok']
    assert '-价格上涨' in payload['data']['diff']
    assert '+价格回落' in payload['data']['diff']
    panel = client.get('/api/brief-diff', params={'before': 'before', 'after': 'after'}).json()
    assert payload['data']['diff'] == panel['diff']
    assert '+110 美元' in panel['diff']
    detail = call(client, 'geo_brief_get', {'brief_id': 'after'})
    assert detail['data']['brief']['markdown'].endswith('110 美元')
    with store.db() as conn:
        assert conn.execute('SELECT markdown FROM briefs WHERE id=?', ('after',)).fetchone()[0].endswith('110 USD')
    truncated = call(client, 'geo_brief_compare', {'before': 'before', 'after': 'after', 'max_bytes': 10})
    assert truncated['data']['truncated']
    assert len(truncated['data']['diff'].encode()) <= 10
    parts, offset = [], 0
    while True:
        page = call(client, 'geo_brief_compare', {'before': 'before', 'after': 'after', 'max_bytes': 10, 'offset_chars': offset})['data']
        parts.append(page['diff'])
        if not page['has_more']:
            break
        assert page['next_offset_chars'] > offset
        offset = page['next_offset_chars']
    assert ''.join(parts) == panel['diff']
    assert page['total_bytes'] == len(panel['diff'].encode())
    assert not call(client, 'geo_brief_compare', {'before': 'missing', 'after': 'after'})['ok']


def test_specific_old_batch_is_not_hidden_by_recent_list_limit(business_client):
    client, root = business_client
    for index in range(25):
        folder = root / 'collection-runs' / f'batch-{index:02d}'
        folder.mkdir(parents=True)
        (folder / 'state.json').write_text(json.dumps({'run_id': folder.name, 'status': 'running',
            'started_at': f'2026-09-{index+1:02d}T00:00:00Z', 'results': []}))
    payload = call(client, 'geo_collector_status', {'run_id': 'batch-00', 'compare_run_id': 'batch-24', 'limit': 1})
    assert payload['ok'], payload
    assert payload['data']['run_id'] == 'batch-00'
    assert payload['data']['comparison']['before_run_id'] == 'batch-24'
    assert not call(client, 'geo_collector_status', {'run_id': '../../secret'})['ok']
