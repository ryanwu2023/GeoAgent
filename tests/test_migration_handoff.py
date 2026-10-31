import json
import sqlite3
from pathlib import Path

import pytest

from backend.store import db
from scripts.migrate_sqlite_to_postgres import backup_sources, main, migrate, source_inventory


def legacy(root):
    root.mkdir()
    with sqlite3.connect(root / 'research.sqlite3') as c:
        c.executescript('''
        CREATE TABLE snapshots(id TEXT PRIMARY KEY,created_at TEXT,payload TEXT);
        CREATE TABLE workspace_state(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT);
        CREATE TABLE answers(id TEXT PRIMARY KEY,snapshot_id TEXT,query TEXT,created_at TEXT,payload TEXT);
        CREATE TABLE briefs(id TEXT PRIMARY KEY,snapshot_id TEXT,created_at TEXT,mode TEXT,markdown TEXT);
        CREATE TABLE translations(hash TEXT PRIMARY KEY,original TEXT,chinese TEXT,method TEXT,created_at TEXT);
        ''')
        payload = json.dumps({'records': [], 'observations': [], 'sources': [], 'errors': [],
                              'meetings': [{'id': 'meeting', 'title': '已有会晤'}]}, ensure_ascii=False)
        c.execute('INSERT INTO snapshots VALUES (?,?,?)', ('snapshot', 'now', payload))
        c.execute('INSERT INTO workspace_state VALUES (?,?,?)', ('working_snapshot_id', 'snapshot', 'now'))
        c.execute('INSERT INTO answers VALUES (?,?,?,?,?)', ('answer', 'snapshot', '问题', 'now', '{"cost":{"estimated_cost_cny":0.12}}'))
        c.execute('INSERT INTO briefs VALUES (?,?,?,?,?)', ('brief', 'snapshot', 'now', 'daily', '# 原有简报'))
        c.execute('INSERT INTO translations VALUES (?,?,?,?,?)', ('hash', 'meeting', '会晤', 'model', 'now'))
    (root / 'qa-agent-settings.json').write_text('{"max_parallel":2}', encoding='utf-8')
    (root / 'archive').mkdir()
    (root / 'archive' / 'evidence.txt').write_text('原始证据', encoding='utf-8')


def test_cli_import_backup_then_verify_preserves_payloads_and_files(tmp_path, capsys):
    root, backup = tmp_path / 'data', tmp_path / 'backup'
    legacy(root)
    original = (root / 'research.sqlite3').read_bytes()
    assert main(['--data-dir', str(root), '--backup-dir', str(backup), '--source-stopped']) == 0
    report = json.loads((backup / 'migration-report.json').read_text())
    assert report['ok'] and report['rows_verified']
    assert report['tables']['answers'] == report['tables']['briefs'] == report['tables']['translations'] == 1
    assert (root / 'research.sqlite3').read_bytes() == original
    assert (backup / 'data' / 'qa-agent-settings.json').read_bytes() == (root / 'qa-agent-settings.json').read_bytes()
    assert (backup / 'data' / 'archive' / 'evidence.txt').read_text() == '原始证据'
    assert any(x['file']=='data/research.sqlite3' for x in json.loads((backup / 'backup-manifest.json').read_text()))
    assert main(['--data-dir', str(backup / 'data'), '--verify-only', '--source-stopped']) == 0
    with db() as c:
        snapshot = json.loads(c.execute('SELECT payload FROM snapshots').fetchone()[0])
        assert snapshot['meetings'][0]['title'] == '已有会晤'
        assert c.execute('SELECT value FROM workspace_state').fetchone()[0] == 'snapshot'
        assert json.loads(c.execute('SELECT payload FROM answers').fetchone()[0])['cost']['estimated_cost_cny'] == 0.12
    assert main(['--data-dir', str(root), '--backup-dir', str(tmp_path / 'second'), '--source-stopped']) == 1
    assert json.loads((tmp_path / 'second/migration-report.json').read_text())['error_type'] == 'RuntimeError'


def test_verify_detects_same_count_changed_content(tmp_path):
    root = tmp_path / 'data'; legacy(root)
    migrate(root)
    with db() as c:
        c.execute("UPDATE answers SET query='被改变的问题'")
    with pytest.raises(RuntimeError, match='Row validation failed: answers'):
        migrate(root, verify_only=True)


def test_check_only_works_without_postgres_and_rejects_unknown_schema(tmp_path, monkeypatch):
    root = tmp_path / 'data'; legacy(root)
    monkeypatch.delenv('GEO_DATABASE_URL', raising=False)
    assert main(['--data-dir', str(root), '--check-only']) == 0
    with sqlite3.connect(root / 'research.sqlite3') as c:
        c.execute('CREATE TABLE unexpected(value TEXT)')
    with pytest.raises(RuntimeError, match='Unmapped'):
        source_inventory(root)


def test_backup_external_database_and_reject_nested_or_existing_directory(tmp_path):
    root = tmp_path / 'data'; legacy(root)
    external = tmp_path / 'custom.sqlite3'
    with sqlite3.connect(external) as c:
        c.execute('CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT)')
        c.execute("INSERT INTO schema_meta VALUES ('version','1')")
    backup = tmp_path / 'backup'
    saved, internal = backup_sources(root, backup, external)
    assert internal.read_bytes() == external.read_bytes()
    assert source_inventory(saved, internal)[-1]['tables']['internal_schema_meta'] == 1
    with pytest.raises(FileExistsError):
        backup_sources(root, backup)
    with pytest.raises(ValueError, match='outside'):
        backup_sources(root, root / 'backup')


def test_cli_requires_stopped_writers_and_backup(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(['--data-dir', str(tmp_path)])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(['--data-dir', str(tmp_path), '--source-stopped'])
    assert exc.value.code == 2


def test_existing_backup_report_is_not_overwritten(tmp_path):
    root, backup = tmp_path / 'data', tmp_path / 'backup'; legacy(root)
    backup.mkdir(); report = backup / 'migration-report.json'; report.write_text('original report')
    assert main(['--data-dir', str(root), '--backup-dir', str(backup), '--source-stopped']) == 1
    assert report.read_text() == 'original report'
