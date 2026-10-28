"""Import stopped legacy SQLite stores into an empty PostgreSQL schema.

Run: python -m scripts.migrate_sqlite_to_postgres --data-dir /path/to/data
     --backup-dir /new/backup --source-stopped
Original files are never changed. Includes research, collector ledger and the
optional internal-message store; all stores are imported in one transaction.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

from psycopg import sql
from backend.database import connect

RESEARCH_TABLES = (
    "snapshots", "records", "reviews", "briefs", "brief_templates", "brief_metadata",
    "answers", "analyses", "page_summaries", "snapshot_stats", "workspace_state",
    "translations", "integration_mcp_keys", "mcp_call_audit",
    "qa_runs", "qa_steps", "qa_artifacts",
)
LEDGER_TABLES = ("collector_runs", "collector_events", "collector_reviews")
INTERNAL_TABLES = {
    "schema_meta": "internal_schema_meta",
    "messages": "internal_messages",
    "message_tags": "internal_message_tags",
    "collection_runs": "internal_collection_runs",
}


def row_digest(row):
    return hashlib.sha256(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode()).digest()


def source_stores(data_dir: Path, internal_message_db: Path | None = None):
    configured = internal_message_db or os.getenv("HT_MACRO_DB_PATH", "").strip()
    internal_path = Path(configured) if configured else data_dir / "ht-macro/messages.sqlite3"
    if configured and not internal_path.is_file():
        raise FileNotFoundError(internal_path)
    sources = [(data_dir / "research.sqlite3", dict.fromkeys(RESEARCH_TABLES)),
               (data_dir / "collection-runs/ledger.sqlite3", dict.fromkeys(LEDGER_TABLES)),
               (internal_path, INTERNAL_TABLES)]
    if not sources[0][0].is_file():
        raise FileNotFoundError(sources[0][0])
    return sources


def open_source(stack, path, allowed):
    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    stack.callback(source.close)
    source.execute("BEGIN")
    if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError(f"Integrity check failed: {path.name}")
    if source.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError(f"Foreign key check failed: {path.name}")
    existing = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if existing - set(allowed):
        raise RuntimeError(f"Unmapped source tables: {sorted(existing - set(allowed))}")
    if path.name == "research.sqlite3" and "snapshots" not in existing:
        raise RuntimeError("research.sqlite3 has no snapshots table; wrong or empty source database")
    return source, existing


def source_inventory(data_dir: Path, internal_message_db: Path | None = None):
    """Read-only preflight; never opens PostgreSQL or prints row contents."""
    inventory = []
    with ExitStack() as stack:
        for path, allowed in source_stores(data_dir, internal_message_db):
            if not path.is_file():
                inventory.append({"file": str(path), "present": False, "optional": True})
                continue
            source, existing = open_source(stack, path, allowed)
            counts = {allowed[t] or t: source.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                      for t in allowed if t in existing}
            inventory.append({"file": str(path), "present": True, "tables": counts})
    return inventory


def migrate(data_dir: Path, internal_message_db: Path | None = None, *, verify_only=False):
    sources = source_stores(data_dir, internal_message_db)
    counts = {}
    with ExitStack() as stack, connect() as target:
        target.execute("SELECT pg_advisory_xact_lock(71004)")
        # Prevent writes during the copy; no existing destination data is overwritten.
        tables = RESEARCH_TABLES + LEDGER_TABLES + tuple(INTERNAL_TABLES.values())
        target.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
            sql.SQL(",").join(map(sql.Identifier, tables))))
        for table in tables:
            if not verify_only and target.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table))).fetchone():
                raise RuntimeError(f"Destination table {table} is not empty; migration aborted")
        for path, allowed in sources:
            if not path.exists():
                continue
            source, existing = open_source(stack, path, allowed)
            for table in allowed:
                if table not in existing:
                    continue
                destination_table = allowed[table] or table
                columns = [r[1] for r in source.execute(f'PRAGMA table_info("{table}")')]
                query = sql.SQL("COPY {} ({}) FROM STDIN").format(sql.Identifier(destination_table), sql.SQL(",").join(map(sql.Identifier, columns)))
                count, checksum = 0, 0
                if verify_only:
                    for row in source.execute(f'SELECT * FROM "{table}"'):
                        count += 1
                        checksum += int.from_bytes(row_digest(row), "big")
                else:
                    with target.cursor().copy(query) as copy:
                        for row in source.execute(f'SELECT * FROM "{table}"'):
                            copy.write_row(row)
                            count += 1
                            checksum += int.from_bytes(row_digest(row), "big")
                verified_count, verified_checksum = 0, 0
                with target.cursor(name="verify_migration") as cursor:
                    cursor.execute(sql.SQL("SELECT {} FROM {}").format(sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(destination_table)))
                    for row in cursor:
                        verified_count += 1
                        verified_checksum += int.from_bytes(row_digest(row.values()), "big")
                if (count, checksum) != (verified_count, verified_checksum):
                    raise RuntimeError(f"Row validation failed: {table}")
                counts[destination_table] = count
        if not verify_only:
            for table in ("mcp_call_audit", "collector_events"):
                target.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE(MAX(id),1), COUNT(*) > 0) FROM {}").format(sql.Identifier(table)), (table,))
    return counts


def backup_sources(data_dir: Path, backup_dir: Path, internal_message_db: Path | None = None):
    """Copy the entire stopped data tree and any separately configured database."""
    data_dir, backup_dir = data_dir.resolve(), backup_dir.resolve()
    if backup_dir == data_dir or data_dir in backup_dir.parents:
        raise ValueError("Backup directory must be outside the source data directory")
    stores = source_stores(data_dir, internal_message_db)
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copytree(data_dir, backup_dir / "data", symlinks=True)
    internal = stores[-1][0].resolve()
    if internal.is_file():
        try:
            relative = internal.relative_to(data_dir)
            internal_backup = backup_dir / "data" / relative
        except ValueError:
            internal_backup = backup_dir / "external-internal-messages" / internal.name
            internal_backup.parent.mkdir()
            for suffix in ("", "-wal", "-shm"):
                source = Path(str(internal) + suffix)
                if source.is_file():
                    shutil.copy2(source, Path(str(internal_backup) + suffix))
    else:
        internal_backup = None
    manifest = []
    for file in sorted(backup_dir.rglob("*")):
        if file.is_file() and not file.is_symlink():
            with file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            manifest.append({"file": file.relative_to(backup_dir).as_posix(), "bytes": file.stat().st_size, "sha256": digest})
    (backup_dir / "backup-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup_dir / "data", internal_backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--internal-message-db", type=Path, help="Explicit legacy internal-message SQLite file (otherwise HT_MACRO_DB_PATH or the default data path)")
    parser.add_argument("--env-file", type=Path, help="Load KEY=VALUE settings; existing process environment wins")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-only", action="store_true", help="Validate source SQLite files and list counts without connecting to PostgreSQL")
    modes.add_argument("--verify-only", action="store_true", help="Compare source rows to PostgreSQL without importing; run before starting the application")
    parser.add_argument("--source-stopped", action="store_true", help="Confirm all legacy/new application and background writers have been stopped")
    parser.add_argument("--backup-dir", type=Path, help="New backup directory outside --data-dir; required for import")
    parser.add_argument("--report", type=Path, help="Write a JSON result (no credentials or row contents)")
    args = parser.parse_args(argv)
    if not args.check_only and not args.source_stopped:
        parser.error("Stop all application/collector/translation writers, then pass --source-stopped")
    if not args.check_only and not args.verify_only and not args.backup_dir:
        parser.error("Import requires --backup-dir pointing to a NEW directory outside --data-dir")
    if args.env_file:
        for line in args.env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    report = {"ok": False, "mode": "check" if args.check_only else "verify" if args.verify_only else "import"}
    report_path = args.report
    try:
        report["sources"] = source_inventory(args.data_dir, args.internal_message_db)
        if not args.check_only:
            data_dir, internal = args.data_dir, args.internal_message_db
            if not args.verify_only:
                data_dir, internal = backup_sources(data_dir, args.backup_dir, internal)
                report_path = report_path or args.backup_dir / "migration-report.json"
                # Always migrate the backup copy, never the live source files.
                # An absent optional file must not resolve through the old environment path.
                old_internal = os.environ.pop("HT_MACRO_DB_PATH", None)
            try:
                report["tables"] = migrate(data_dir, internal, verify_only=args.verify_only)
            finally:
                if not args.verify_only and old_internal is not None:
                    os.environ["HT_MACRO_DB_PATH"] = old_internal
            report["rows_verified"] = True
            report["schema"] = os.getenv("GEO_DATABASE_SCHEMA", "public")
        report["ok"] = True
    except Exception as exc:
        # Error details can contain source rows. Keep them out of shareable reports.
        report["error_type"] = type(exc).__name__
        print(f"Migration stopped ({type(exc).__name__}). Original SQLite files were retained; check source schema, target emptiness and database connectivity.", file=sys.stderr)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
