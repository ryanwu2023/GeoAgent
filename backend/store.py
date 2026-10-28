import hashlib
import json
import os
from functools import lru_cache
from .database import connect
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

def db():
    return connect()

def snapshots():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT id,created_at FROM snapshots ORDER BY created_at DESC")]

def get_working_snapshot_id():
    """Return the persisted snapshot selected as the workspace context."""
    with db() as c:
        row = c.execute(
            "SELECT value FROM workspace_state WHERE key = %s",
            ("working_snapshot_id",),
        ).fetchone()
    return str(row["value"]) if row else None

def set_working_snapshot_id(snapshot_id: str):
    """Persist a workspace snapshot only when it exists in the immutable store."""
    with db() as c:
        c.execute("SELECT pg_advisory_xact_lock(71002)")
        if c.execute("SELECT 1 FROM snapshots WHERE id = %s", (snapshot_id,)).fetchone() is None:
            raise KeyError("快照不存在")
        c.execute(
            """INSERT INTO workspace_state(key,value,updated_at) VALUES (%s,%s,%s)
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
        # Backfill legacy snapshots inside PostgreSQL so Python never materializes
        # every immutable archive at once. Production archives can be hundreds
        # of megabytes and previously caused MemoryError here.
        c.execute("""
            INSERT INTO snapshot_stats
            SELECT id,
                   COALESCE(jsonb_array_length(payload::jsonb -> 'records'), 0),
                   COALESCE(jsonb_array_length(payload::jsonb -> 'observations'), 0),
                   COALESCE(jsonb_array_length(payload::jsonb -> 'sources'), 0),
                   COALESCE(jsonb_array_length(payload::jsonb -> 'errors'), 0),
                   COALESCE((payload::jsonb -> 'cleaning')::text, '{}'),
                   COALESCE((payload::jsonb -> 'import_stats')::text, '{}')
            FROM snapshots s
            WHERE NOT EXISTS (
                SELECT 1 FROM snapshot_stats x WHERE x.snapshot_id=s.id
            ) ON CONFLICT DO NOTHING
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
        row = c.execute("SELECT payload,created_at FROM snapshots WHERE id=%s", (sid,)).fetchone()
    if not row:
        raise KeyError("快照不存在")
    return {**json.loads(row["payload"]), "id": sid, "created_at": row["created_at"]}

def save_snapshot(payload):
    sid = digest(payload)
    timestamp = now()
    with db() as c:
        c.execute("SELECT pg_advisory_xact_lock(71002)")
        if c.execute("SELECT 1 FROM snapshots WHERE id=%s", (sid,)).fetchone():
            return sid, False
        new_count = revised_count = 0
        for record in payload["records"]:
            exists = c.execute("SELECT version FROM records WHERE id=%s", (record["id"],)).fetchall()
            version = digest(record)
            new_count += not bool(exists)
            revised_count += bool(exists) and version not in [x[0] for x in exists]
            c.execute("INSERT INTO records VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (record["id"], version, timestamp, canonical(record)))
        payload = {**payload, "import_stats": {"new": new_count, "revised": revised_count, "total": len(payload["records"])}}
        c.execute("INSERT INTO snapshots VALUES (%s,%s,%s)", (sid, timestamp, canonical(payload)))
        c.execute(
            "INSERT INTO snapshot_stats VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                sid, len(payload.get("records", [])), len(payload.get("observations", [])),
                len(payload.get("sources", [])), len(payload.get("errors", [])),
                canonical(payload.get("cleaning", {})), canonical(payload.get("import_stats", {})),
            ),
        )
    return sid, True
