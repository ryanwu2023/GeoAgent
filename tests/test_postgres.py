from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest
import psycopg

from backend.database import connect
from backend.store import save_snapshot, snapshots, snapshot_summaries, db
from scripts.migrate_sqlite_to_postgres import migrate


def test_transaction_rolls_back_on_error():
    with pytest.raises(ValueError):
        with db() as conn:
            conn.execute("INSERT INTO workspace_state VALUES ('rollback','x','now')")
            raise ValueError("abort")
    with db() as conn:
        assert conn.execute("SELECT * FROM workspace_state WHERE key='rollback'").fetchone() is None


def test_concurrent_snapshot_import_is_idempotent():
    payload = {"records": [{"id": "r", "title": "原始证据"}], "sources": [], "observations": [], "errors": []}
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(lambda _: save_snapshot(payload), range(16)))
    assert len({sid for sid, _ in result}) == 1
    assert sum(created for _, created in result) == 1
    assert len(snapshots()) == 1


def test_json_stats_backfill():
    sid, _ = save_snapshot({"records": [], "sources": [], "observations": [], "errors": [], "cleaning": {"说明": "保留"}})
    with db() as conn:
        conn.execute("DELETE FROM snapshot_stats WHERE snapshot_id=%s", (sid,))
    summary = snapshot_summaries()[0]
    assert summary["records"] == 0
    assert summary["cleaning"] == {"说明": "保留"}


def legacy_fixture(root):
    with sqlite3.connect(root / "research.sqlite3") as source:
        source.executescript("""
        CREATE TABLE snapshots(id TEXT PRIMARY KEY,created_at TEXT,payload TEXT);
        INSERT INTO snapshots VALUES ('old','2026-01-01','{"records":[],"sources":[],"observations":[],"errors":[]}');
        CREATE TABLE integration_mcp_keys(id TEXT PRIMARY KEY,label TEXT,token_hash TEXT,created_at TEXT,revoked_at TEXT);
        INSERT INTO integration_mcp_keys VALUES ('key','旧凭据','hash-only','2026-01-01',NULL);
        CREATE TABLE mcp_call_audit(id INTEGER PRIMARY KEY,created_at TEXT,tool TEXT,status TEXT,duration_ms INTEGER,audit_event_id TEXT);
        INSERT INTO mcp_call_audit VALUES (41,'2026-01-01','tool','ok',1,'event');
        """)
    (root / "collection-runs").mkdir()
    with sqlite3.connect(root / "collection-runs/ledger.sqlite3") as source:
        source.executescript("""
        CREATE TABLE collector_runs(run_id TEXT PRIMARY KEY,run_kind TEXT,status TEXT,started_at TEXT);
        INSERT INTO collector_runs VALUES ('run','historical','success','2026-01-01');
        CREATE TABLE collector_events(id INTEGER PRIMARY KEY,run_id TEXT,event_type TEXT,created_at TEXT);
        INSERT INTO collector_events VALUES (51,'run','started','2026-01-01');
        """)


def legacy_internal_fixture(path):
    with sqlite3.connect(path) as source:
        source.executescript("""
        CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT);
        INSERT INTO schema_meta VALUES ('schema_version','1');
        CREATE TABLE messages(public_id TEXT PRIMARY KEY,dedupe_key TEXT,
            message_time_ms INTEGER,upstream_message_id TEXT,first_seen_at TEXT,
            last_seen_at TEXT,content_hash TEXT,raw_json TEXT);
        INSERT INTO messages VALUES ('htm-old','unq:old',1792629453000,
            '2683648938485114610','now','now','hash','{"title":"历史消息"}');
        CREATE TABLE message_tags(public_id TEXT,tag_name TEXT);
        INSERT INTO message_tags VALUES ('htm-old','原油');
        CREATE TABLE collection_runs(run_id TEXT PRIMARY KEY,started_at TEXT,status TEXT);
        INSERT INTO collection_runs VALUES ('internal-run','now','succeeded');
        """)


def test_internal_message_migration_custom_path_preserves_all_stores(tmp_path):
    legacy_fixture(tmp_path)
    path = tmp_path / "custom.sqlite3"
    legacy_internal_fixture(path)
    before = path.read_bytes()
    counts = migrate(tmp_path, path)
    assert counts["internal_messages"] == counts["internal_message_tags"] == 1
    assert counts["internal_collection_runs"] == counts["internal_schema_meta"] == 1
    assert path.read_bytes() == before
    with db() as conn:
        row = conn.execute("SELECT * FROM internal_messages").fetchone()
        assert row["message_time_ms"] == 1792629453000
        assert row["upstream_message_id"] == "2683648938485114610"
        assert conn.execute("SELECT run_id FROM collector_runs").fetchone()[0] == "run"
        assert conn.execute("SELECT run_id FROM internal_collection_runs").fetchone()[0] == "internal-run"


def test_internal_message_failure_rolls_back_research_and_ledger(tmp_path, monkeypatch):
    legacy_fixture(tmp_path)
    path = tmp_path / "custom.sqlite3"
    legacy_internal_fixture(path)
    monkeypatch.setenv("HT_MACRO_DB_PATH", str(path))
    with sqlite3.connect(path) as source:
        source.execute("UPDATE message_tags SET public_id='missing'")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrate(tmp_path)
    with db() as conn:
        for table in ("snapshots", "collector_runs", "internal_messages"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_explicit_missing_internal_store_is_not_silently_skipped(tmp_path):
    legacy_fixture(tmp_path)
    with pytest.raises(FileNotFoundError):
        migrate(tmp_path, tmp_path / "missing.sqlite3")


def test_legacy_migration_preserves_rows_and_identity(tmp_path):
    legacy_fixture(tmp_path)
    before = (tmp_path / "research.sqlite3").read_bytes()
    counts = migrate(tmp_path)
    assert counts["snapshots"] == 1 and counts["collector_events"] == 1
    assert (tmp_path / "research.sqlite3").read_bytes() == before
    with db() as conn:
        key = conn.execute("SELECT * FROM integration_mcp_keys").fetchone()
        assert key["token_hash"] == "hash-only" and key["deleted_at"] is None
        assert conn.execute("INSERT INTO mcp_call_audit(tool) VALUES ('next') RETURNING id").fetchone()[0] == 42
        assert conn.execute("INSERT INTO collector_events(run_id,event_type,created_at) VALUES ('run','next','now') RETURNING id").fetchone()[0] == 52
    with pytest.raises(RuntimeError, match="not empty"):
        migrate(tmp_path)


def test_failed_migration_rolls_back_all_tables(tmp_path):
    legacy_fixture(tmp_path)
    with sqlite3.connect(tmp_path / "collection-runs/ledger.sqlite3") as source:
        source.execute("UPDATE collector_events SET run_id='missing'")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrate(tmp_path)
    assert snapshots() == []
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM integration_mcp_keys").fetchone()[0] == 0


def test_legacy_migration_preserves_new_qa_tables(tmp_path):
    legacy_fixture(tmp_path)
    with sqlite3.connect(tmp_path / "research.sqlite3") as source:
        source.executescript("""
        CREATE TABLE qa_runs(run_id TEXT PRIMARY KEY,answer_id TEXT,snapshot_id TEXT,
            scope_hash TEXT,phase TEXT,status TEXT,created_at TEXT,updated_at TEXT,payload TEXT);
        INSERT INTO qa_runs VALUES ('run-qa','answer-qa','old','scope','research','completed','now','now','{}');
        CREATE TABLE qa_steps(run_id TEXT,sequence INTEGER,created_at TEXT,payload TEXT,PRIMARY KEY(run_id,sequence));
        INSERT INTO qa_steps VALUES ('run-qa',1,'now','{"message":"历史问答步骤"}');
        CREATE TABLE qa_artifacts(artifact_id TEXT PRIMARY KEY,run_id TEXT,call_id TEXT,
            created_at TEXT,byte_count INTEGER,token_estimate INTEGER,preview TEXT,payload TEXT);
        INSERT INTO qa_artifacts VALUES ('artifact','run-qa','call','now',2,1,'{}','{}');
        """)
    counts = migrate(tmp_path)
    assert {table: counts[table] for table in ("qa_runs", "qa_steps", "qa_artifacts")} == {
        "qa_runs": 1, "qa_steps": 1, "qa_artifacts": 1,
    }
    with db() as conn:
        assert conn.execute("SELECT payload FROM qa_steps WHERE run_id=%s", ("run-qa",)).fetchone()[0] == '{"message":"历史问答步骤"}'
        assert conn.execute("SELECT call_id FROM qa_artifacts WHERE artifact_id=%s", ("artifact",)).fetchone()[0] == "call"


def test_postgres_url_is_required(monkeypatch):
    monkeypatch.delenv("GEO_DATABASE_URL")
    with pytest.raises(RuntimeError, match="GEO_DATABASE_URL"):
        with connect():
            pass
