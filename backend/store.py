import hashlib
import json
import os
import sqlite3
from functools import lru_cache
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def now():
    return datetime.now(timezone.utc).isoformat()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:24]

def data_dir():
    return Path(os.getenv("GEO_DATA_DIR", str(ROOT / "data")))

@contextmanager
def db():
    data_dir().mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(data_dir() / "research.sqlite3", timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS reviews(snapshot_id TEXT, record_id TEXT, version INTEGER, signal TEXT, reviewer TEXT, reason TEXT, created_at TEXT, PRIMARY KEY(snapshot_id,record_id,version));
    CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS records(id TEXT NOT NULL, version TEXT NOT NULL, first_imported_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(id,version));
    CREATE TABLE IF NOT EXISTS briefs(id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES snapshots(id), created_at TEXT NOT NULL, mode TEXT NOT NULL, markdown TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS brief_templates(id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL, is_default INTEGER NOT NULL DEFAULT 0, is_system INTEGER NOT NULL DEFAULT 0, config TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS brief_metadata(brief_id TEXT PRIMARY KEY REFERENCES briefs(id), template_id TEXT NOT NULL, template_version INTEGER NOT NULL, template_config TEXT NOT NULL, model TEXT, window_start TEXT, window_end TEXT, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, query TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, scope TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS page_summaries(cache_key TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES snapshots(id), page TEXT NOT NULL, scope TEXT NOT NULL, created_at TEXT NOT NULL, model TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS snapshot_stats(snapshot_id TEXT PRIMARY KEY REFERENCES snapshots(id), records INTEGER NOT NULL, observations INTEGER NOT NULL, sources INTEGER NOT NULL, errors INTEGER NOT NULL, cleaning TEXT NOT NULL, import_stats TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS workspace_state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS qa_runs(
        run_id TEXT PRIMARY KEY,
        answer_id TEXT,
        snapshot_id TEXT NOT NULL,
        scope_hash TEXT NOT NULL,
        phase TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS qa_steps(
        run_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY(run_id,sequence)
    );
    CREATE TABLE IF NOT EXISTS qa_artifacts(
        artifact_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        call_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        byte_count INTEGER NOT NULL,
        token_estimate INTEGER NOT NULL,
        preview TEXT NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_qa_steps_run ON qa_steps(run_id,sequence);
    CREATE INDEX IF NOT EXISTS idx_qa_artifacts_run_call ON qa_artifacts(run_id,call_id);
    """)
    try:
        yield c
        c.commit()
    finally:
        c.close()

def snapshots():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT id,created_at FROM snapshots ORDER BY created_at DESC")]

def get_working_snapshot_id():
    """Return the persisted snapshot selected as the workspace context."""
    with db() as c:
        row = c.execute(
            "SELECT value FROM workspace_state WHERE key = ?",
            ("working_snapshot_id",),
        ).fetchone()
    return str(row["value"]) if row else None

def set_working_snapshot_id(snapshot_id: str):
    """Persist a workspace snapshot only when it exists in the immutable store."""
    with db() as c:
        if c.execute("SELECT 1 FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone() is None:
            raise KeyError("快照不存在")
        c.execute(
            """INSERT INTO workspace_state(key,value,updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            ("working_snapshot_id", snapshot_id, now()),
        )
    return snapshot_id

def snapshot_summaries():
    """Return lightweight, read-only statistics for the snapshot picker.

    The UI needs version lineage without downloading every record in each
    immutable payload.  Keep this projection derived from the stored payload;
    it is not a second source of truth.
    """
    with db() as c:
        # Backfill legacy snapshots inside SQLite so Python never materializes
        # every immutable archive at once. Production archives can be hundreds
        # of megabytes and previously caused MemoryError here.
        c.execute("""
            INSERT OR IGNORE INTO snapshot_stats
            SELECT id,
                   COALESCE(json_array_length(payload, '$.records'), 0),
                   COALESCE(json_array_length(payload, '$.observations'), 0),
                   COALESCE(json_array_length(payload, '$.sources'), 0),
                   COALESCE(json_array_length(payload, '$.errors'), 0),
                   COALESCE(json_extract(payload, '$.cleaning'), '{}'),
                   COALESCE(json_extract(payload, '$.import_stats'), '{}')
            FROM snapshots s
            WHERE NOT EXISTS (
                SELECT 1 FROM snapshot_stats x WHERE x.snapshot_id=s.id
            )
        """)
        rows = c.execute("""
            SELECT s.id,s.created_at,x.records,x.observations,x.sources,x.errors,
                   x.cleaning,x.import_stats
            FROM snapshots s JOIN snapshot_stats x ON x.snapshot_id=s.id
            ORDER BY s.created_at DESC
        """).fetchall()
    return [{
        "id": row["id"], "created_at": row["created_at"],
        "records": row["records"], "observations": row["observations"],
        "sources": row["sources"], "errors": row["errors"],
        "cleaning": json.loads(row["cleaning"] or "{}"),
        "import_stats": json.loads(row["import_stats"] or "{}"),
    } for row in rows]

@lru_cache(maxsize=2)
def get_snapshot(sid):
    """Load an immutable snapshot once per process.

    Snapshot ids are content hashes, so a cached payload can never be changed by
    a later import.  This avoids repeatedly decoding 20+ MB JSON blobs while a
    researcher moves between views of the same snapshot.
    """
    with db() as c:
        row = c.execute("SELECT payload,created_at FROM snapshots WHERE id=?", (sid,)).fetchone()
    if not row:
        raise KeyError("快照不存在")
    return {**json.loads(row["payload"]), "id": sid, "created_at": row["created_at"]}

def save_snapshot(payload):
    sid = digest(payload)
    timestamp = now()
    with db() as c:
        if c.execute("SELECT 1 FROM snapshots WHERE id=?", (sid,)).fetchone():
            return sid, False
        new_count = revised_count = 0
        for record in payload["records"]:
            exists = c.execute("SELECT version FROM records WHERE id=?", (record["id"],)).fetchall()
            version = digest(record)
            new_count += not bool(exists)
            revised_count += bool(exists) and version not in [x[0] for x in exists]
            c.execute("INSERT OR IGNORE INTO records VALUES (?,?,?,?)", (record["id"], version, timestamp, canonical(record)))
        payload = {**payload, "import_stats": {"new": new_count, "revised": revised_count, "total": len(payload["records"])}}
        c.execute("INSERT INTO snapshots VALUES (?,?,?)", (sid, timestamp, canonical(payload)))
        c.execute(
            "INSERT INTO snapshot_stats VALUES (?,?,?,?,?,?,?)",
            (
                sid, len(payload.get("records", [])), len(payload.get("observations", [])),
                len(payload.get("sources", [])), len(payload.get("errors", [])),
                canonical(payload.get("cleaning", {})), canonical(payload.get("import_stats", {})),
            ),
        )
    return sid, True
