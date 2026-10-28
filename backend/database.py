"""PostgreSQL connections, transaction ownership and serialized schema setup."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import threading

import psycopg
from psycopg import sql


class Row(dict):
    """Named rows with positional access for existing query projections."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


def database_url():
    value = os.getenv("GEO_DATABASE_URL", "")
    if not value.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("GEO_DATABASE_URL must be a PostgreSQL connection URL")
    return value


def database_schema():
    value = os.getenv("GEO_DATABASE_SCHEMA", "public")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", value):
        raise ValueError("Invalid GEO_DATABASE_SCHEMA")
    return value


_initialized = set()
_schema_lock = threading.Lock()


@contextmanager
def connect():
    """Commit successful work; rollback failures and always close connections."""
    dsn, schema = database_url(), database_schema()
    with psycopg.connect(dsn, row_factory=row_factory, connect_timeout=10) as connection:
        connection.execute(sql.SQL("SET search_path TO {} ").format(sql.Identifier(schema)))
        key = (dsn, schema)
        if key not in _initialized:
            with _schema_lock:
                if key not in _initialized:
                    connection.execute("SELECT pg_advisory_xact_lock(71001)")
                    connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
                    connection.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
                    connection.commit()
                    _initialized.add(key)
        yield connection
