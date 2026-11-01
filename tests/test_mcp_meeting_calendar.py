"""Meeting calendar over the real in-process MCP protocol, with disposable snapshot data."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import store
from backend.app import app


def meeting(event_id, *, status="confirmed", topics=None, date_start="2026-10-27", track="us-ir", person="putin"):
    names = {"putin": ("普京", "俄罗斯总统", "俄罗斯"), "pezeshkian": ("佩泽希齐扬", "伊朗总统", "伊朗")}
    name, role, country = names[person]
    return {
        "id": event_id, "evidence_id": "meeting-" + event_id, "title": "领导人会晤", "track": track,
        "topics": topics or ["usiran"], "date_start": date_start, "date_end": "", "date_raw": date_start,
        "precision": "day", "location": "莫斯科", "points": [{"label": "莫斯科", "lon": 37.6, "lat": 55.7}],
        "persons": [{"id": person, "name": name, "role": role, "country": country}],
        "status": status, "status_label": "已确认", "inactive": status in {"canceled", "lapsed"},
        "claims": [{"text": "双方确认将会晤"}], "sources": [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}],
        "updates": [{"at": "2026-10-20", "text": "日期确认"}], "conflict": False,
    }


@pytest.fixture
def meeting_client(tmp_path, monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR', str(tmp_path))
    snapshot = {'id': 'snap', 'created_at': '2026-10-25T00:00:00Z', 'records': [], 'meetings': [
        meeting('EV-1'),
        meeting('EV-2', status='canceled'),
        meeting('EV-3', topics=['ukraine'], date_start='2026-11-02', track='ru-ua'),
        meeting('EV-4', date_start='2026-10-29', person='pezeshkian', status='scheduled'),
        meeting('EV-5', date_start='2027-06-01'),
    ]}
    monkeypatch.setattr(store, 'get_snapshot', lambda sid: snapshot if sid == 'snap' else (_ for _ in ()).throw(KeyError(sid)))
    monkeypatch.setattr(store, 'get_working_snapshot_id', lambda: 'snap')
    monkeypatch.setattr(store, 'snapshot_summaries', lambda: [{'id': 'snap', 'created_at': snapshot['created_at']}])
    with TestClient(app) as client:
        yield client


def call(client, name, arguments):
    response = client.post('/api/integrations/mcp-workbench/call', json={'tool': name, 'arguments': arguments})
    assert response.status_code == 200
    return json.loads(response.json()['result']['content'][0]['text'])


def test_meeting_list_matches_calendar_defaults_and_keeps_rows_small(meeting_client):
    payload = call(meeting_client, 'geo_meeting_list', {'snapshot_id': 'snap'})
    assert payload['ok'] is True
    data = payload['data']
    # Default window: snapshot import day + 120 days, inactive excluded, sorted by date.
    assert [row['id'] for row in data['meetings']] == ['EV-1', 'EV-4', 'EV-3']
    assert data['query_scope']['start'] == '2026-10-25'
    assert data['query_scope']['end'] == '2027-02-22'
    assert data['status_counts'] == {'confirmed': 2, 'scheduled': 1}
    assert data['tracks'] == {'us-ir': 2, 'ru-ua': 1}
    row = data['meetings'][0]
    assert row['persons'] == [{'name': '普京', 'role': '俄罗斯总统', 'country': '俄罗斯'}]
    assert row['source_count'] == 2
    for heavy in ('claims', 'sources', 'updates', 'points'):
        assert heavy not in row
    assert '不等于会晤必然举行' in data['basis']
    assert payload['provenance']['snapshot_id'] == 'snap'


def test_meeting_list_filters_topic_person_status_inactive_and_pages(meeting_client):
    usiran = call(meeting_client, 'geo_meeting_list', {'snapshot_id': 'snap', 'topic': 'usiran'})['data']
    assert {row['id'] for row in usiran['meetings']} == {'EV-1', 'EV-4'}
    person = call(meeting_client, 'geo_meeting_list', {'snapshot_id': 'snap', 'person': '佩泽希齐扬'})['data']
    assert [row['id'] for row in person['meetings']] == ['EV-4']
    status = call(meeting_client, 'geo_meeting_list', {'snapshot_id': 'snap', 'status': 'scheduled,confirmed', 'track': 'us-ir'})['data']
    assert [row['id'] for row in status['meetings']] == ['EV-1', 'EV-4']
    inactive = call(meeting_client, 'geo_meeting_list', {'snapshot_id': 'snap', 'topic': 'usiran', 'include_inactive': True})['data']
    assert {row['id'] for row in inactive['meetings']} == {'EV-1', 'EV-2', 'EV-4'}
    page = call(meeting_client, 'geo_meeting_list', {'snapshot_id': 'snap', 'limit': 1, 'offset': 1})['data']
    assert [row['id'] for row in page['meetings']] == ['EV-4']
    assert page['total'] == 3 and page['has_more'] is True
    later = call(meeting_client, 'geo_meeting_list', {'snapshot_id': 'snap', 'start': '2027-01-01', 'end': '2027-12-31'})['data']
    assert [row['id'] for row in later['meetings']] == ['EV-5']


def test_meeting_list_rejects_bad_input(meeting_client):
    bad_date = call(meeting_client, 'geo_meeting_list', {'snapshot_id': 'snap', 'start': '2026-02-30'})
    assert bad_date['ok'] is False
    bad_offset = call(meeting_client, 'geo_meeting_list', {'snapshot_id': 'snap', 'offset': -1})
    assert bad_offset['ok'] is False and bad_offset['error']['code'] == 'GEO_INVALID_OFFSET'
    missing = call(meeting_client, 'geo_meeting_list', {'snapshot_id': 'other'})
    assert missing['ok'] is False


def test_meeting_get_returns_full_record_scoped_to_snapshot(meeting_client):
    detail = call(meeting_client, 'geo_meeting_get', {'snapshot_id': 'snap', 'meeting_id': 'EV-1'})
    assert detail['ok'] is True
    record = detail['data']['meeting']
    assert record['claims'] == [{'text': '双方确认将会晤'}]
    assert len(record['sources']) == 2 and record['evidence_id'] == 'meeting-EV-1'
    assert '计划地点而非人物实时位置' in detail['data']['basis']
    missing = call(meeting_client, 'geo_meeting_get', {'snapshot_id': 'snap', 'meeting_id': 'EV-X'})
    assert missing['ok'] is False and missing['error']['code'] == 'GEO_MEETING_NOT_FOUND'


def test_business_catalog_routes_meeting_questions(meeting_client):
    catalog = call(meeting_client, 'geo_business_catalog', {})['data']
    assert catalog['questions']['近期有哪些关键会晤/某人物的会晤安排'][0] == 'geo_meeting_list'
