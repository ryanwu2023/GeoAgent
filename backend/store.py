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
    CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, query TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, scope TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
    """)
    try:
        yield c
        c.commit()
    finally:
        c.close()

def snapshots():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT id,created_at FROM snapshots ORDER BY created_at DESC")]

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
    return sid, True
