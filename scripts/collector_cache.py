"""Atomic public-response checkpoints. No credentials are stored in keys or metadata."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


class ResponseCache:
    def __init__(self, directory: Path | None, run_id: str = ''):
        self.directory = Path(directory) if directory else None
        self.run_id = run_id

    @staticmethod
    def key(url: str, body: bytes | None = None, variant: str = '') -> str:
        return hashlib.sha256(json.dumps([url, body.hex() if body is not None else None, variant]).encode()).hexdigest()

    def get(self, key: str) -> dict:
        if self.directory is None:
            return {}
        try:
            path = self.directory / f'{key}.json'
            if path.stat().st_size > 12 * 1024 * 1024:
                return {}
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('saved_at'), (int, float)):
                return {}
            if not 0 <= time.time() - data['saved_at'] <= 7 * 86400:
                return {}
            return data
        except (OSError, ValueError, TypeError):
            return {}

    def same_run(self, entry: dict) -> bool:
        return bool(self.run_id and entry.get('run_id') == self.run_id
                    and 0 <= time.time() - entry.get('saved_at', 0) < 6 * 3600)

    def put(self, key: str, response: dict) -> None:
        if self.directory is None:
            return
        value = json.dumps({**response, 'run_id': self.run_id, 'saved_at': time.time()}, ensure_ascii=False)
        if len(value.encode('utf-8')) > 12 * 1024 * 1024:
            return
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.response-', dir=self.directory)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(value)
            os.replace(temporary, self.directory / f'{key}.json')
        except OSError:
            # A cache is optional: disk errors cannot turn a successful fetch into failure.
            pass
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
