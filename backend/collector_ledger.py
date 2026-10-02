"""Persistent execution ledger and single-instance lock for collectors.

The collector reports remain the compatibility output consumed by the current
UI.  This module adds a small SQLite sidecar so the service can answer a
different question reliably: what the scheduler is doing, and what happened to
each collector in a run.  SQLite is intentionally local and single-host for
Phase 1; a multi-host deployment should move this contract to PostgreSQL.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS collector_runs (
    run_id TEXT PRIMARY KEY,
    run_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    topic TEXT,
    timeout_sec INTEGER,
    expected INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    error_message TEXT,
    import_status TEXT,
    snapshot_id TEXT,
    import_error_type TEXT,
    import_error_message TEXT,
    gate_decision TEXT,
    gate_reviewer TEXT,
    gate_reason TEXT,
    gate_at TEXT
);

CREATE TABLE IF NOT EXISTS collector_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    collector_id TEXT,
    event_type TEXT NOT NULL,
    status TEXT,
    message TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES collector_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_collector_events_run_created
    ON collector_events(run_id, created_at, id);

CREATE TABLE IF NOT EXISTS collector_reviews (
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reviewer TEXT,
    reason TEXT,
    evidence_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, item_id),
    FOREIGN KEY (run_id) REFERENCES collector_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_collector_reviews_run_updated
    ON collector_reviews(run_id, updated_at);
"""


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


class CollectorLedger:
    """A small append-oriented run/event ledger backed by SQLite."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.path) as connection:
            connection.executescript(SCHEMA)
            # The ledger is a sidecar that can survive service upgrades.  Keep
            # old local databases readable without asking users to delete run
            # history when import observability is added.
            existing = {
                row[1]
                for row in connection.execute("PRAGMA table_info(collector_runs)").fetchall()
            }
            for name, definition in (
                ("import_status", "TEXT"),
                ("snapshot_id", "TEXT"),
                ("import_error_type", "TEXT"),
                ("import_error_message", "TEXT"),
                ("gate_decision", "TEXT"),
                ("gate_reviewer", "TEXT"),
                ("gate_reason", "TEXT"),
                ("gate_at", "TEXT"),
            ):
                if name not in existing:
                    connection.execute(f"ALTER TABLE collector_runs ADD COLUMN {name} {definition}")

    def run_started(
        self,
        *,
        run_id: str,
        run_kind: str,
        started_at: str,
        topic: str | None,
        timeout_sec: int,
    ) -> None:
        with _connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO collector_runs
                    (run_id, run_kind, status, started_at, topic, timeout_sec)
                VALUES (?, ?, 'running', ?, ?, ?)
                """,
                (run_id, run_kind, started_at, topic, timeout_sec),
            )

    def event(
        self,
        *,
        run_id: str,
        event_type: str,
        status: str | None = None,
        collector_id: str | None = None,
        message: str | None = None,
        payload: dict | list | None = None,
        created_at: str | None = None,
    ) -> None:
        payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        with _connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO collector_events
                    (run_id, collector_id, event_type, status, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    collector_id,
                    event_type,
                    status,
                    message[:2000] if isinstance(message, str) else message,
                    payload_json,
                    created_at or utc_timestamp(),
                ),
            )

    def run_finished(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: str,
        expected: int,
        completed: int,
        failed: int,
        error_type: str | None = None,
        error_message: str | None = None,
        import_status: str | None = None,
        snapshot_id: str | None = None,
        import_error_type: str | None = None,
        import_error_message: str | None = None,
    ) -> None:
        with _connect(self.path) as connection:
            connection.execute(
                """
                UPDATE collector_runs
                SET status = ?, finished_at = ?, expected = ?, completed = ?, failed = ?,
                    error_type = ?, error_message = ?, import_status = ?, snapshot_id = ?,
                    import_error_type = ?, import_error_message = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    finished_at,
                    expected,
                    completed,
                    failed,
                    error_type,
                    error_message[:2000] if isinstance(error_message, str) else error_message,
                    import_status,
                    snapshot_id,
                    import_error_type,
                    import_error_message[:2000] if isinstance(import_error_message, str) else import_error_message,
                    run_id,
                ),
            )

    def update_import(
        self,
        *,
        run_id: str,
        import_status: str,
        snapshot_id: str | None = None,
        import_error_type: str | None = None,
        import_error_message: str | None = None,
    ) -> None:
        """Update import outcome without changing the collection outcome."""
        with _connect(self.path) as connection:
            connection.execute(
                """
                UPDATE collector_runs
                SET import_status = ?, snapshot_id = ?,
                    import_error_type = ?, import_error_message = ?
                WHERE run_id = ?
                """,
                (
                    import_status,
                    snapshot_id,
                    import_error_type,
                    import_error_message[:2000] if isinstance(import_error_message, str) else import_error_message,
                    run_id,
                ),
            )

    def update_gate(
        self,
        *,
        run_id: str,
        decision: str,
        reviewer: str,
        reason: str | None = None,
        decided_at: str | None = None,
    ) -> None:
        if decision not in {"approved", "rejected"}:
            raise ValueError("gate decision must be approved or rejected")
        with _connect(self.path) as connection:
            connection.execute(
                """
                UPDATE collector_runs
                SET gate_decision = ?, gate_reviewer = ?, gate_reason = ?, gate_at = ?
                WHERE run_id = ?
                """,
                (decision, reviewer, reason, decided_at or utc_timestamp(), run_id),
            )

    def ensure_run(
        self,
        *,
        run_id: str,
        run_kind: str = "historical-report",
        started_at: str | None = None,
    ) -> None:
        """Create a minimal parent row so old reports can receive reviews."""
        with _connect(self.path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO collector_runs
                    (run_id, run_kind, status, started_at)
                VALUES (?, ?, 'historical', ?)
                """,
                (run_id, run_kind, started_at or utc_timestamp()),
            )

    def reviews(self, run_id: str) -> list[dict]:
        with _connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT run_id, item_id, status, reviewer, reason,
                       evidence_json, updated_at
                FROM collector_reviews
                WHERE run_id = ?
                ORDER BY item_id
                """,
                (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if item.get("evidence_json"):
                try:
                    item["evidence"] = json.loads(item["evidence_json"])
                except json.JSONDecodeError:
                    item["evidence"] = None
            item.pop("evidence_json", None)
            result.append(item)
        return result

    def upsert_review(
        self,
        *,
        run_id: str,
        item_id: str,
        status: str,
        reviewer: str | None = None,
        reason: str | None = None,
        evidence: dict | list | None = None,
        updated_at: str | None = None,
    ) -> dict:
        if status not in {"pending", "accepted", "rejected"}:
            raise ValueError("review status must be pending, accepted, or rejected")
        timestamp = updated_at or utc_timestamp()
        evidence_json = json.dumps(evidence, ensure_ascii=False) if evidence is not None else None
        with _connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO collector_reviews
                    (run_id, item_id, status, reviewer, reason, evidence_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, item_id) DO UPDATE SET
                    status = excluded.status,
                    reviewer = excluded.reviewer,
                    reason = excluded.reason,
                    evidence_json = excluded.evidence_json,
                    updated_at = excluded.updated_at
                """,
                (run_id, item_id, status, reviewer, reason, evidence_json, timestamp),
            )
        return next(item for item in self.reviews(run_id) if item["item_id"] == item_id)

    def recover_unfinished(self, *, finished_at: str, exclude_run_id: str | None = None) -> list[str]:
        """Mark runs left running by a killed service as explicitly abandoned."""
        with _connect(self.path) as connection:
            query = "SELECT run_id FROM collector_runs WHERE status = 'running'"
            params: tuple[str, ...] = ()
            if exclude_run_id:
                query += " AND run_id != ?"
                params = (exclude_run_id,)
            rows = connection.execute(query, params).fetchall()
            run_ids = [row[0] for row in rows]
            for run_id in run_ids:
                connection.execute(
                    """
                    UPDATE collector_runs
                    SET status = 'abandoned', finished_at = ?, error_type = ?,
                        error_message = ?, import_status = COALESCE(import_status, ?)
                    WHERE run_id = ? AND status = 'running'
                    """,
                    (
                        finished_at,
                        "SERVICE_INTERRUPTED",
                        "服务进程在运行中断开，未完成本轮采集",
                        "not_attempted_interrupted",
                        run_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO collector_events
                        (run_id, event_type, status, message, created_at)
                    VALUES (?, 'run_abandoned', 'abandoned', ?, ?)
                    """,
                    (run_id, "服务进程中断，运行记录已标记为 abandoned", finished_at),
                )
        return run_ids

    def run(self, run_id: str) -> dict | None:
        with _connect(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM collector_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def runs(self, limit: int = 20) -> list[dict]:
        with _connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM collector_runs
                ORDER BY started_at DESC, run_id DESC
                LIMIT ?
                """,
                (max(0, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def events(self, run_id: str, limit: int = 100) -> list[dict]:
        with _connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, collector_id, event_type, status, message,
                       payload_json, created_at
                FROM collector_events
                WHERE run_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (run_id, max(0, limit)),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if item.get("payload_json"):
                try:
                    item["payload"] = json.loads(item["payload_json"])
                except json.JSONDecodeError:
                    item["payload"] = None
            item.pop("payload_json", None)
            result.append(item)
        return result


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_owner(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


@contextmanager
def service_lock(path: Path) -> Iterator[None]:
    """Acquire a cross-platform single-instance lock.

    ``O_EXCL`` makes acquisition atomic on local filesystems.  A lock left by a
    crashed process is removed only when its recorded PID is no longer alive;
    malformed locks are left in place for manual inspection.
    """

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    for attempt in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            owner = _lock_owner(lock_path)
            pid = owner.get("pid")
            if attempt == 0 and isinstance(pid, int) and not _pid_alive(pid):
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            detail = f" pid={pid}" if pid is not None else ""
            raise RuntimeError(f"collector service lock is held: {lock_path}{detail}")
    if fd is None:
        raise RuntimeError(f"unable to acquire collector service lock: {lock_path}")
    try:
        metadata = {"pid": os.getpid(), "created_at": utc_timestamp()}
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False)
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
