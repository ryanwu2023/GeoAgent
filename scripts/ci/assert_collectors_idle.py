"""Fail closed on busy/unknown state; reserve the collector lock during rollout."""
import json
import os
import signal
import sys
import time


def _lock_path():
    from backend.store import data_dir
    return data_dir() / 'collection-runs/.collector-service.lock'


def _assert_ledger_idle():
    from backend.store import db
    with db() as connection:
        rows = connection.execute("SELECT run_id FROM collector_runs WHERE status='running'").fetchall()
    if rows:
        raise RuntimeError('采集运行中，禁止发布: ' + ', '.join(row['run_id'] for row in rows))


def assert_idle():
    from backend.collector_ledger import _lock_owner, _pid_alive
    lock = _lock_path()
    if lock.exists():
        pid = _lock_owner(lock).get('pid')
        if not isinstance(pid, int) or _pid_alive(pid):
            raise RuntimeError('采集运行中或锁状态未知；禁止发布，请等待本轮结束')
    _assert_ledger_idle()
    print('Collectors idle: deployment preflight passed', flush=True)


def hold_release(token):
    from backend.collector_ledger import service_lock, _lock_owner
    lock = _lock_path()
    # Same atomic acquisition used by both the manual API and scheduled service.
    # Old images already implement this lock, so first rollout is protected too.
    with service_lock(lock):
        _assert_ledger_idle()
        owner = _lock_owner(lock)
        lock.write_text(json.dumps({**owner, 'deployment_token': token}), encoding='utf-8')
        print('Collector deployment guard acquired', flush=True)
        # Rollout is bounded to 300s. If CI dies before patching, never block forever.
        time.sleep(600)


def release(token):
    from backend.collector_ledger import _lock_owner, _pid_alive
    owner = _lock_owner(_lock_path())
    pid = owner.get('pid')
    if owner.get('deployment_token') == token and isinstance(pid, int) and _pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
    # SIGTERM leaves a stale lock; service_lock safely reclaims it after PID exit.


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--hold':
        hold_release(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == '--release':
        release(sys.argv[2])
    elif len(sys.argv) == 1:
        assert_idle()
    else:
        raise SystemExit('Usage: assert_collectors_idle.py [--hold|--release token]')
