"""Small adapters for legacy collectors: successful-response resume and bounded IO."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import threading
from urllib.parse import urlsplit

from scripts.collector_cache import ResponseCache


class HttpRuntime:
    def __init__(self):
        state = os.getenv('COLLECTOR_STATE_ROOT')
        self.cache = ResponseCache(Path(state) / 'http-cache' if state else None,
                                   os.getenv('COLLECTOR_RUN_ID', ''))
        self._locks = {}
        self._guard = threading.Lock()

    def fetch(self, fetcher, url, *, timeout=30, min_iv=1.2, ua=None, retries=1, valid_body=None):
        def usable(body):
            return isinstance(body, str) and bool(body.strip()) and (valid_body is None or valid_body(body))
        # Include retries and original host pacing inside the same host lock.
        host = urlsplit(url).netloc
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            key = self.cache.key(url, variant=ua or 'Mozilla/5.0')
            saved = self.cache.get(key)
            if (self.cache.same_run(saved) and saved.get('status') == 200
                    and usable(saved.get('body'))):
                return {'status': 200, 'body': saved['body'], 'ms': 0,
                        'final_url': saved.get('final_url', url),
                        'proxy': 'checkpoint', 'from_cache': True}
            result = fetcher(url, timeout=timeout, min_iv=min_iv, ua=ua, retries=retries)
            if result.get('status') == 200 and usable(result.get('body')):
                # Never persist proxy URLs, credentials, errors or request headers.
                self.cache.put(key, {k: result[k] for k in ('status', 'body', 'final_url') if k in result})
            return result


def source_results(fetcher, sources, config):
    """All sources, stable order, at most three outstanding source workers."""
    with ThreadPoolExecutor(max_workers=min(3, max(1, len(sources))),
                            thread_name_prefix='source') as pool:
        yield from pool.map(lambda source: fetcher(source, config), sources)


def prepare_output(explicit, legacy):
    target = Path(explicit or os.getenv('COLLECTOR_OUTPUT_ROOT') or legacy)
    target.mkdir(parents=True, exist_ok=True)
    previous = os.getenv('COLLECTOR_PREVIOUS_OUTPUT_ROOT')
    if not explicit and os.getenv('COLLECTOR_OUTPUT_ROOT') and previous:
        previous = Path(previous)
        if previous.is_dir() and previous.resolve() != target.resolve():
            for old in previous.rglob('*'):
                if old.is_file() and not old.is_symlink():
                    new = target / old.relative_to(previous)
                    if not new.exists():
                        new.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(old, new)
    return str(target)
