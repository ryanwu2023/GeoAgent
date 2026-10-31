"""Regression/performance contracts: preserve scope while avoiding duplicate IO."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def load(relative, name):
    p = ROOT / relative
    sys.path.insert(0, str(p.parent))
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_spending_managed_paths_and_previous_month_cache(tmp_path, monkeypatch):
    state, stage, previous = [tmp_path / x for x in ['state', 'stage', 'previous']]
    monkeypatch.setenv('COLLECTOR_STATE_ROOT', str(state))
    monkeypatch.setenv('COLLECTOR_OUTPUT_ROOT', str(stage))
    monkeypatch.setenv('COLLECTOR_PREVIOUS_OUTPUT_ROOT', str(previous))
    m = load('collectors/defense-spend-monitor/spend_monitor.py', 'perf_spend_paths')
    assert m.STATE_DIR == state
    assert m.OUTPUT_DIR == stage
    (previous / 'history').mkdir(parents=True)
    months = [f'2026-{n:02d}' for n in range(1, 11)]
    cells = {ym: {term: 7 for term, _ in m.FR_HIST_TERMS} for ym in months}
    (previous / 'history/fr-monthly.json').write_text(json.dumps({'cells': cells}))
    calls = []
    class Fetcher:
        def get(self, url, **kw):
            calls.append(url)
            return b'{"count": 8}'
    m.HISTORY_CFG = {'refresh_recent_months': 2}
    m.build_history({'history': {'start': '2026-01-01'}}, {}, [], stage,
                    datetime(2026, 10, 31, tzinfo=timezone.utc), Fetcher(), False)
    assert len(calls) == 9
    persisted = json.loads((state / 'history/fr-monthly.json').read_text())
    assert persisted['cells']['2026-01']['munitions'] == 7
    assert persisted['cells']['2026-10']['munitions'] == 8


def test_spending_retry_reuses_success_but_new_run_refreshes(tmp_path, monkeypatch):
    monkeypatch.setenv('COLLECTOR_STATE_ROOT', str(tmp_path))
    monkeypatch.setenv('COLLECTOR_RUN_ID', 'run-a')
    m = load('collectors/defense-spend-monitor/spend_monitor.py', 'perf_spend_http')
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request.data)
            if request.full_url.endswith('/bad'):
                raise TimeoutError('upstream unavailable')
            class Response(io.BytesIO):
                headers = {}
            return Response(b'{"value": 42}')
    monkeypatch.setattr(m.Fetcher, '_opener', lambda self: Opener())
    a = m.Fetcher({'retries': 1})
    assert a.post_json('https://example.test/data', {'month': 1}) == b'{"value": 42}'
    b = m.Fetcher({'retries': 1})
    assert b.post_json('https://example.test/data', {'month': 1}) == b'{"value": 42}'
    assert len(calls) == 1  # second process/attempt must not download successful request again
    b.post_json('https://example.test/data', {'month': 2})
    assert len(calls) == 2
    assert b.get('https://example.test/bad', verbose=False) is None
    assert m.Fetcher({'retries': 1}).get('https://example.test/bad', verbose=False) is None
    assert len(calls) == 4
    monkeypatch.setenv('COLLECTOR_RUN_ID', 'run-b')
    m.Fetcher({'retries': 1}).post_json('https://example.test/data', {'month': 1})
    assert len(calls) == 5


def test_geo_http_cross_process_cache_revalidation_and_retry(tmp_path, monkeypatch):
    monkeypatch.setenv('COLLECTOR_STATE_ROOT', str(tmp_path))
    monkeypatch.setenv('COLLECTOR_RUN_ID', 'one')
    web = load('collectors/geopolitical-source-monitor/web.py', 'perf_geo_web')
    calls = []
    def transport(request, timeout):
        calls.append(dict(request.header_items()))
        if len(calls) == 1:
            return web.TransportResponse(200, {'ETag': '"v1"'}, b'original body', request.full_url)
        return web.TransportResponse(304, {}, b'', request.full_url)
    def client():
        return web.PolicyHttpClient(user_agent='test', transport=transport,
                                   robots_fetcher=lambda _: 'User-agent: *\nAllow: /')
    source = {'allowed_hosts': ['example.test'], 'rate_limit_seconds': 0, 'max_retries': 0}
    assert client().fetch('https://example.test/a', source).body == 'original body'
    assert client().fetch('https://example.test/a', source).body == 'original body'
    assert len(calls) == 1
    monkeypatch.setenv('COLLECTOR_RUN_ID', 'two')
    result = client().fetch('https://example.test/a', source)
    assert result.body == 'original body' and result.from_cache
    assert calls[-1].get('If-none-match') == '"v1"'
    assert len(calls) == 2


def test_geo_source_parallelism_preserves_every_record(tmp_path, monkeypatch):
    m = load('collectors/geopolitical-source-monitor/geopolitical_source_monitor.py', 'perf_geo_run')
    config = json.loads((ROOT / 'collectors/geopolitical-source-monitor/config.json').read_text())
    base = config['sources'][0]
    config['sources'] = [{**base, 'id': f's{i}', 'entry_urls': [f'https://s{i}.test/list'],
                          'allowed_hosts': [f's{i}.test'], 'content_policy': 'metadata_only'} for i in range(4)]
    config['request']['source_workers'] = 3
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    lock = threading.Lock()
    class Client:
        active = 0
        maximum = 0
        def fetch(self, url, source):
            with lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            time.sleep(0.06)
            with lock: self.active -= 1
            return m.FetchResult(200, '<a href="/news/iran">Iran missile exercise near Hormuz</a>', url, {})
    client = Client()
    # Real parsing and classification; fake only external network latency.
    parallel = m.run_collection(path, tmp_path / 'parallel', client)
    assert client.maximum > 1
    config['request']['source_workers'] = 1
    path.write_text(json.dumps(config))
    serial = m.run_collection(path, tmp_path / 'serial', Client())
    a = json.loads((tmp_path / 'parallel/geo-v6.json').read_text())
    b = json.loads((tmp_path / 'serial/geo-v6.json').read_text())
    assert parallel['accepted'] == serial['accepted'] == 4
    assert {x['id'] for x in a['records']} == {x['id'] for x in b['records']}
    assert list(parallel['sources']) == list(serial['sources']) == ['s0', 's1', 's2', 's3']


def test_same_host_requests_remain_serial_and_rate_limited(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setenv('COLLECTOR_STATE_ROOT', str(tmp_path))
    web = load('collectors/geopolitical-source-monitor/web.py', 'perf_geo_host')
    active = 0
    maximum = 0
    starts = []
    lock = threading.Lock()
    def transport(request, timeout):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(active, maximum)
            starts.append(time.monotonic())
        time.sleep(0.02)
        with lock: active -= 1
        return web.TransportResponse(200, {}, b'ok', request.full_url)
    client = web.PolicyHttpClient(user_agent='test', transport=transport, robots_fetcher=lambda _: 'User-agent: *\nAllow: /')
    source = {'allowed_hosts': ['same.test'], 'rate_limit_seconds': 0.04}
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda i: client.fetch(f'https://same.test/{i}', source), range(3)))
    assert [x.body for x in results] == ['ok'] * 3
    assert maximum == 1
    assert all(b - a >= 0.055 for a, b in zip(starts, starts[1:]))


def test_real_child_retry_reuses_completed_http_request(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from scripts.collect import run_one
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"records": [1, 2]}')
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    src, data = tmp_path / 'src', tmp_path / 'data'
    (src / 'demo').mkdir(parents=True)
    (src / 'demo/main.py').write_text(f'''import sys,os,importlib.util
from pathlib import Path
p={str(ROOT / 'collectors/defense-spend-monitor/spend_monitor.py')!r}
spec=importlib.util.spec_from_file_location('spend',p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
body=m.Fetcher({{'retries':1}}).get('http://127.0.0.1:{server.server_port}/data')
state=Path(os.environ['COLLECTOR_STATE_ROOT']);state.mkdir(parents=True,exist_ok=True)
flag=state/'attempt'
if not flag.exists(): flag.write_text('1'); sys.exit(1)
Path(os.environ['COLLECTOR_OUTPUT_ROOT'],'geo-v6.json').write_bytes(body)
''')
    monkeypatch.setattr('scripts.collect.collector_source_root', lambda: src)
    monkeypatch.setenv('COLLECTOR_DATA_ROOT', str(data))
    try:
        result = run_one({'id': 'demo', 'entrypoint': 'main.py'}, timeout=10, max_attempts=2, sleep=lambda _: None,
                         staging_root=data / '.staging' / 'batch-a')
        assert result['returncode'] == 0 and result['attempts'] == 2
        assert requests == ['/data']
        assert json.loads((data / 'demo/output/geo-v6.json').read_text()) == {'records': [1, 2]}
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_deploy_idle_check_blocks_running_ledger_and_lock(tmp_path, monkeypatch):
    import pytest
    from backend import store
    from backend.collector_ledger import CollectorLedger, service_lock
    from scripts.ci.assert_collectors_idle import assert_idle
    monkeypatch.setenv('GEO_DATA_DIR', str(tmp_path))
    assert_idle()
    lock = tmp_path / 'collection-runs/.collector-service.lock'
    with service_lock(lock):
        with pytest.raises(RuntimeError, match='采集|collect'):
            assert_idle()
    ledger = CollectorLedger()
    ledger.run_started(run_id='active', run_kind='test', started_at='2026-10-31T00:00:00Z', topic=None, timeout_sec=900)
    with pytest.raises(RuntimeError, match='active'):
        assert_idle()
    with store.db() as c: c.execute("UPDATE collector_runs SET status='success' WHERE run_id='active'")
    assert_idle()


def test_deploy_idle_check_fails_closed_on_database_error(monkeypatch):
    import pytest
    from scripts.ci.assert_collectors_idle import assert_idle
    monkeypatch.setenv('GEO_DATABASE_URL', 'postgresql://invalid:invalid@127.0.0.1:1/none')
    with pytest.raises(Exception): assert_idle()


def test_previous_geo_records_keep_raw_evidence_when_publishing_new_stage(tmp_path, monkeypatch):
    m = load('collectors/geopolitical-source-monitor/geopolitical_source_monitor.py', 'perf_previous_geo')
    config = json.loads((ROOT / 'collectors/geopolitical-source-monitor/config.json').read_text())
    config['sources'] = config['sources'][:1]
    p = tmp_path / 'config.json'; p.write_text(json.dumps(config))
    previous = tmp_path / 'previous'; (previous / 'raw/old').mkdir(parents=True)
    (previous / 'raw/old/article.html').write_text('original evidence')
    (previous / 'geo-v6.json').write_text(json.dumps({'records': [{
        'id': 'old', 'title': 'Iran missile exercise near Hormuz', 'body': 'Iran missile exercise',
        'link': 'https://example.test/old', 'language': 'en', 'published': '2026-10-30',
        'source_id': 'official', 'raw_files': ['raw/old/article.html'], 'content_access': 'public_fulltext',
    }]}))
    monkeypatch.setenv('COLLECTOR_PREVIOUS_OUTPUT_ROOT', str(previous))
    output = tmp_path / 'stage'
    class EmptySource:
        def fetch(self, url, source): return m.FetchResult(200, '<html></html>', url, {})
    result = m.run_collection(p, output, EmptySource())
    assert result['records'] == 1
    assert (output / 'raw/old/article.html').read_text() == 'original evidence'


def test_corrupt_checkpoint_falls_back_to_network(tmp_path, monkeypatch):
    from scripts.collector_cache import ResponseCache
    monkeypatch.setenv('COLLECTOR_STATE_ROOT', str(tmp_path))
    monkeypatch.setenv('COLLECTOR_RUN_ID', 'run')
    cache = ResponseCache(tmp_path / 'http-cache', 'run')
    cache.put(cache.key('https://example.test/a', variant='test'), {'body': {'corrupt': True}})
    web = load('collectors/geopolitical-source-monitor/web.py', 'perf_corrupt_geo')
    client = web.PolicyHttpClient(user_agent='test', robots_fetcher=lambda _: 'User-agent: *\nAllow: /',
        transport=lambda req, _: web.TransportResponse(200, {}, b'recovered', req.full_url))
    assert client.fetch('https://example.test/a', {'allowed_hosts': ['example.test']}).body == 'recovered'


def test_rate_limit_includes_robots_and_retries(tmp_path):
    web = load('collectors/geopolitical-source-monitor/web.py', 'perf_retry_spacing')
    starts = []
    clock = [100.0]
    calls = []
    def transport(request, timeout):
        starts.append(clock[0]); calls.append(request.full_url)
        return web.TransportResponse(503 if len(calls) == 2 else 200, {}, b'User-agent: *\nAllow: /', request.full_url)
    from unittest.mock import patch
    with patch.object(web.time, 'monotonic', lambda: clock[0]):
        client = web.PolicyHttpClient(user_agent='test', transport=transport,
            sleep=lambda delay: clock.__setitem__(0, clock[0] + delay), cache_dir=tmp_path)
        result = client.fetch('https://example.test/a', {'allowed_hosts': ['example.test'], 'rate_limit_seconds': 3, 'max_retries': 1})
    assert result.status == 200
    assert len(starts) == 3
    assert all(b - a >= 3 for a, b in zip(starts, starts[1:]))


def test_month_cache_retries_missing_terms_and_preserves_success(tmp_path, monkeypatch):
    m = load('collectors/defense-spend-monitor/spend_monitor.py', 'perf_partial_month')
    months = [f'2026-{n:02d}' for n in range(1, 5)]
    path = tmp_path / 'months.json'
    terms = [term for term, label in m.FR_HIST_TERMS]
    path.write_text(json.dumps({'cells': {ym: {term: 7 for term in terms} for ym in months}}))
    data = json.loads(path.read_text()); del data['cells']['2026-01'][terms[0]]; path.write_text(json.dumps(data))
    calls = []
    class Fetcher:
        def get(self, url, **kw):
            calls.append(url)
            if '2026-01' in url: return b'{"count": 8}'
            return None  # failed refresh must not erase successful prior cells
    result = m.fr_month_counts(Fetcher(), months, path, False)
    assert result['2026-01'][terms[0]] == 8
    assert result['2026-04'] == {term: 7 for term in terms}
    assert len([url for url in calls if '2026-01' in url]) == 1


def test_same_article_updated_body_replaces_equal_rank_previous():
    m = load('collectors/geopolitical-source-monitor/geopolitical_source_monitor.py', 'perf_corrected_article')
    config = json.loads((ROOT / 'collectors/geopolitical-source-monitor/config.json').read_text())
    old = {'id': 'x', 'source_id': 'official', 'title': 'Iran missile exercise near Hormuz',
        'body': 'Iran missile exercise old', 'link': 'https://example.test/news', 'language': 'en',
        'published': '2026-10-31', 'fetched_at': '2026-10-31T01:00:00Z', 'content_access': 'public_fulltext', 'raw_files': ['raw/old.html']}
    new = {**old, 'body': 'Iran missile exercise corrected', 'fetched_at': '2026-10-31T02:00:00Z', 'raw_files': ['raw/new.html']}
    result = m.merge_records([old], [new], config)
    assert len(result) == 1
    assert result[0]['body'] == new['body']
    assert result[0]['raw_files'] == ['raw/new.html']


def test_release_guard_holds_collector_lock_until_released(tmp_path, monkeypatch):
    import select
    import subprocess
    import pytest
    from scripts.ci.assert_collectors_idle import assert_idle
    from backend.collector_jobs import CollectorJobManager, CollectorBusyError
    monkeypatch.setenv('GEO_DATA_DIR', str(tmp_path))
    assert_idle()
    script = ROOT / 'scripts/ci/assert_collectors_idle.py'
    process = subprocess.Popen([sys.executable, str(script), '--hold', 'pipeline-test'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               env={**os.environ, 'PYTHONPATH': str(ROOT)})
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == 'Collector deployment guard acquired'
        with pytest.raises(CollectorBusyError):
            CollectorJobManager().submit(ids=['anything'])
        released = subprocess.run([sys.executable, str(script), '--release', 'pipeline-test'],
                                  capture_output=True, env={**os.environ, 'PYTHONPATH': str(ROOT)})
        assert released.returncode == 0
        process.wait(timeout=5)
        assert_idle()
    finally:
        if process.poll() is None: process.terminate(); process.wait(timeout=5)
