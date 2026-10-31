"""Legacy source pipelines retain coverage while resuming successful HTTP work."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import time
from urllib.error import HTTPError
import pytest

ROOT = Path(__file__).resolve().parents[1]
VALID_FEED = b'<rss><channel><item><title>Fresh</title><link>https://example.test/article</link></item></channel></rss>'
COLLECTORS = [('israel-monitor', 'israel_monitor.py'), ('mideast-monitor', 'mideast_monitor.py')]


def load(pair, suffix):
    path = ROOT / 'collectors' / pair[0] / pair[1]
    spec = importlib.util.spec_from_file_location('legacy_perf_' + pair[0] + suffix, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Response(io.BytesIO):
    status = 200
    headers = {}
    def geturl(self):
        return 'https://example.test/feed'


@pytest.mark.parametrize('pair', COLLECTORS)
def test_retry_reuses_success_and_new_batch_refreshes(pair, tmp_path, monkeypatch):
    monkeypatch.setenv('COLLECTOR_STATE_ROOT', str(tmp_path))
    monkeypatch.setenv('COLLECTOR_RUN_ID', 'batch-a')
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            if request.full_url.endswith('/bad'):
                raise HTTPError(request.full_url, 503, 'unavailable', {}, None)
            return Response(VALID_FEED)
    def instance(name):
        mod = load(pair, name)
        monkeypatch.setattr(mod, 'live_proxies', lambda: [None])
        monkeypatch.setattr(mod, '_opener_for', lambda _: Opener())
        return mod
    first = instance('one')
    assert first.http_get('https://example.test/feed', min_iv=0)['body'] == VALID_FEED.decode()
    retry = instance('retry')
    assert retry.http_get('https://example.test/feed', min_iv=0)['body'] == VALID_FEED.decode()
    assert len(calls) == 1
    for _ in range(2):
        assert retry.http_get('https://example.test/bad', min_iv=0, retries=0)['status'] == 0
    assert len(calls) == 3
    monkeypatch.setenv('COLLECTOR_RUN_ID', 'batch-b')
    instance('new').http_get('https://example.test/feed', min_iv=0)
    assert len(calls) == 4


@pytest.mark.parametrize('pair', COLLECTORS)
def test_main_fetches_every_enabled_source_with_bounded_parallelism(pair, tmp_path, monkeypatch):
    mod = load(pair, 'main')
    enabled = [x for x in mod.load_cfg()['sources'] if x.get('enabled')]
    active = peak = 0
    lock = threading.Lock()
    visited = []
    def fetch(source, cfg):
        nonlocal active, peak
        with lock:
            active += 1; peak = max(peak, active); visited.append(source['id'])
        time.sleep(.015)
        with lock: active -= 1
        return [], {'status': 200}
    monkeypatch.setattr(mod, 'fetch_source', fetch)
    monkeypatch.setattr(mod, 'live_proxies', lambda: [])
    monkeypatch.setattr(sys, 'argv', ['collector', '--out-dir', str(tmp_path)])
    mod.main()
    assert sorted(visited) == sorted(x['id'] for x in enabled)
    assert 1 < peak <= 3
    stats = json.loads((tmp_path / '_fetch_stats.json').read_text())
    assert list(stats['src']) == [x['id'] for x in enabled]


@pytest.mark.parametrize('pair', COLLECTORS)
def test_http_serializes_same_host_but_allows_other_hosts(pair, tmp_path, monkeypatch):
    monkeypatch.setenv('COLLECTOR_STATE_ROOT', str(tmp_path))
    monkeypatch.setenv('COLLECTOR_RUN_ID', 'spacing')
    mod = load(pair, 'locks')
    active = peak = 0
    lock = threading.Lock()
    class Opener:
        def open(self, request, timeout):
            nonlocal active, peak
            with lock: active += 1; peak = max(peak, active)
            time.sleep(.025)
            with lock: active -= 1
            return Response(b'ok')
    monkeypatch.setattr(mod, 'live_proxies', lambda: [None])
    monkeypatch.setattr(mod, '_opener_for', lambda _: Opener())
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda i: mod.http_get(f'https://same.test/{i}', min_iv=0), range(3)))
    assert peak == 1
    peak = 0
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda i: mod.http_get(f'https://other{i}.test/feed', min_iv=0), range(3)))
    assert peak > 1


def test_managed_output_copies_previous_without_overwriting_explicit_path(tmp_path, monkeypatch):
    from scripts.collector_http_runtime import prepare_output
    previous, stage, explicit = [tmp_path / x for x in ['previous', 'stage', 'explicit']]
    (previous / 'raw').mkdir(parents=True)
    (previous / 'ALL-records.jsonl').write_text('{"id":"old"}\n')
    (previous / 'raw/a.txt').write_text('original')
    monkeypatch.setenv('COLLECTOR_OUTPUT_ROOT', str(stage))
    monkeypatch.setenv('COLLECTOR_PREVIOUS_OUTPUT_ROOT', str(previous))
    assert prepare_output(None, tmp_path / 'legacy') == str(stage)
    assert (stage / 'raw/a.txt').read_text() == 'original'
    assert (stage / 'ALL-records.jsonl').read_text() == '{"id":"old"}\n'
    assert prepare_output(str(explicit), tmp_path / 'legacy') == str(explicit)
    assert not (explicit / 'ALL-records.jsonl').exists()


@pytest.mark.parametrize('pair', COLLECTORS)
@pytest.mark.parametrize('failed_body', [b'', b'   ', b'<html>temporary challenge</html>'])
def test_failed_feed_response_is_refetched_on_process_retry(pair, failed_body, tmp_path, monkeypatch):
    monkeypatch.setenv('COLLECTOR_STATE_ROOT', str(tmp_path))
    monkeypatch.setenv('COLLECTOR_RUN_ID', 'feed-retry')
    bodies = [failed_body, b'<rss><channel><item><title>Recovered</title><link>https://example.test/article</link></item></channel></rss>']
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return Response(bodies.pop(0))
    def run(suffix):
        mod = load(pair, suffix)
        monkeypatch.setattr(mod, 'live_proxies', lambda: [None])
        monkeypatch.setattr(mod, '_opener_for', lambda _: Opener())
        return mod.fetch_source({'url': 'https://example.test/feed'}, {'min_interval_per_host': 0})
    assert run('bad')[0] is None
    records, result = run('recovered')
    assert records is not None and records[0]['title'] == 'Recovered'
    assert len(calls) == 2
