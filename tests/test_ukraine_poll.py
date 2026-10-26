import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from backend.adapters import load_bundle
from backend.research import metrics
from backend.semantic import metric_semantics

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ukraine_poll', ROOT / 'collectors/ukraine-opinion-monitor/ukraine_opinion_monitor.py')
poll = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poll)


def release(peace=63, war=26, start=23, end=30, month='сентября'):
    # Synthetic, not a second copy of the live upstream values.
    return (f'<p>Две трети считают, что следует начать мирные переговоры – {peace}%. '
            f'Доля сторонников продолжения военных действий снизилась до {war}%.</p>'
            '<p>Доля сторонников мирных переговоров выше среди женщин (75%), мужчин (45%).</p>'
            f'<p>Общероссийский опрос Левада-Центра проведен с {start} по {end} {month} '
            '2026 года по выборке объемом 1616 человек в возрасте от 18 лет и старше. '
            'Исследование проводится на дому методом личного интервью.</p>').encode()


def feed():
    return b'''<rss><channel><item><title>\xd0\x9a\xd0\xbe\xd0\xbd\xd1\x84\xd0\xbb\xd0\xb8\xd0\xba\xd1\x82 \xd1\x81 \xd0\xa3\xd0\xba\xd1\x80\xd0\xb0\xd0\xb8\xd0\xbd\xd0\xbe\xd0\xb9 \xd0\xb2 September</title>
    <link>https://www.levada.ru/2026/10/06/konflikt-s-ukrainoi-test/</link><pubDate>Tue, 06 Oct 2026 14:00:00 +0000</pubDate></item>
    <item><title>\xd0\x9a\xd0\xbe\xd0\xbd\xd1\x84\xd0\xbb\xd0\xb8\xd0\xba\xd1\x82 \xd1\x81 \xd0\xa3\xd0\xba\xd1\x80\xd0\xb0\xd0\xb8\xd0\xbd\xd0\xbe\xd0\xb9 \xd0\xb2 August</title>
    <link>https://www.levada.ru/2026/09/01/konflikt-s-ukrainoi-test/</link><pubDate>Tue, 01 Sep 2026 14:00:00 +0000</pubDate></item></channel></rss>'''


def fetcher(url):
    if url == poll.FEED: return feed()
    return release() if '/10/06/' in url else release(58, 30, 18, 28, 'августа')


def test_parser_uses_national_totals_and_actual_survey_end_not_publication():
    result = poll.parse_release(release(), published='2026-10-06', today=date(2026,10,20))
    assert result['end'] == '2026-09-30'
    assert result['sample_size'] == 1616
    assert list(result['values'].values()) == [63,26]


@pytest.mark.parametrize('body', [
    release().replace(b'63%', b'163%'), release(90,26),
    release().replace('методом личного интервью'.encode(), b'unknown'),
    release().replace('18 лет и старше'.encode(), b'unknown'),
    release().replace('с 23 по 30 сентября'.encode(), 'с 23 по 31 сентября'.encode()),
    release().replace('Доля сторонников продолжения военных действий'.encode(), 'Другая формулировка'.encode()),
    release() + release(),
])
def test_missing_changed_ambiguous_or_invalid_values_fail_closed(body):
    with pytest.raises((ValueError, KeyError)):
        poll.parse_release(body, published='2026-10-06', today=date(2026,10,20))


def test_collector_import_scope_semantics_and_raw_provenance(monkeypatch, tmp_path):
    output = tmp_path / 'ukraine-opinion-monitor/output'
    monkeypatch.setenv('COLLECTOR_OUTPUT_ROOT',str(output))
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path / 'db'))
    result = poll.collect(__file__, fetcher=fetcher, today=date(2026,10,20))
    assert result['collection_status'] == 'ok'
    bundle = load_bundle(tmp_path)
    selected = metrics(bundle,'ukraine','russia_domestic')
    assert len(selected) == 2
    assert len(metrics(bundle,'all')) == 2
    assert metrics(bundle,'usiran') == []
    assert metrics(bundle,'ukraine','aid') == []
    first = selected[0]
    assert first['current'] == {'date':'2026-09-30','value':63}
    assert first['previous'] == {'date':'2026-08-28','value':58}
    assert first['delta'] == 5 and first['delta_unit'] == '个百分点' and first['pct'] is None
    assert len(first['history']) == 2
    assert '俄罗斯' in first['scope'] and '不是停火概率' in first['boundary']
    assert '俄罗斯' in metric_semantics(first)['meaning']
    assert next(r for r in bundle['records'] if r['id']==first['source_ids'][0])['published_at'] == '2026-10-06'
    assert len(metrics(bundle,'ukraine',start='2026-09-01')[0]['history']) == 1
    assert metrics(bundle,'ukraine',start='2026-10-01')[0]['current'] is None
    for record in bundle['records']:
        assert len(record['raw_refs']) == 2
        assert any(ref.endswith('.html') for ref in record['raw_refs'])
        assert record['published_at'] != first['current']['date']


def test_failed_latest_cannot_fall_back_to_older_wave(monkeypatch,tmp_path):
    monkeypatch.setenv('COLLECTOR_OUTPUT_ROOT',str(tmp_path))
    def broken(url):
        return b'<html>changed layout</html>' if '/10/06/' in url else fetcher(url)
    result = poll.collect(__file__,fetcher=broken,today=date(2026,10,20))
    assert result['collection_status'] == 'failed'
    assert result['observations'] == []
    status = json.loads((tmp_path/'_source_status.json').read_text())
    assert status['sources'][0]['state'] == 'failed'


def test_feed_rejects_non_source_links_and_future_publications():
    with pytest.raises(ValueError):
        poll.releases(feed().replace(b'www.levada.ru', b'evil.example'),date(2026,10,20))
    with pytest.raises(ValueError):
        poll.releases(feed(),date(2026,9,1))


def test_entire_raw_directory_symlink_is_rejected(monkeypatch,tmp_path):
    output = tmp_path/'ukraine-opinion-monitor/output'
    monkeypatch.setenv('COLLECTOR_OUTPUT_ROOT',str(output))
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path/'db'))
    payload = poll.collect(__file__,fetcher=fetcher,today=date(2026,10,20))
    external = tmp_path/'external'
    (output/'raw').rename(external)
    try:
        (output/'raw').symlink_to(external,target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前 Windows 账户不能创建符号链接：{getattr(exc, 'winerror', exc)}")
    with pytest.raises(ValueError,match='扩展原始证据'):
        load_bundle(tmp_path)


def test_frozen_html_is_downloadable_as_inert_text_not_executable(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import app
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path))
    archive = tmp_path/'archive'
    archive.mkdir()
    name = 'a'*24+'.html'
    (archive/name).write_text('<script>never execute</script>')
    response = TestClient(app).get('/api/archive/'+name)
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/plain')
    assert response.headers['content-disposition'].startswith('attachment;')
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert "default-src 'none'" in response.headers['content-security-policy']
    assert response.text == '<script>never execute</script>'


@pytest.mark.parametrize('relative',['../../outside.html','raw/../../../outside.html','raw/symlink.html'])
def test_raw_evidence_cannot_escape_collector(monkeypatch,tmp_path,relative):
    output = tmp_path/'ukraine-opinion-monitor/output'
    monkeypatch.setenv('COLLECTOR_OUTPUT_ROOT',str(output))
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path/'db'))
    payload = poll.collect(__file__,fetcher=fetcher,today=date(2026,10,20))
    outside = tmp_path/'outside.html'
    outside.write_text('outside')
    try:
        (output/'raw/symlink.html').symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"当前 Windows 账户不能创建符号链接：{getattr(exc, 'winerror', exc)}")
    payload['records'][0]['raw_files'] = [relative]
    (output/'geo-v6.json').write_text(json.dumps(payload))
    with pytest.raises(ValueError,match='扩展原始证据'):
        load_bundle(tmp_path)
