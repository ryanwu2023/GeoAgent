"""Real PostgreSQL isolation: tests never inherit the application's database URL."""
import os
import uuid

import psycopg
from psycopg import sql
import pytest


@pytest.fixture(autouse=True)
def postgres_database(monkeypatch, tmp_path):
    dsn = os.getenv("GEO_TEST_DATABASE_URL")
    if not dsn:
        pytest.fail("Set GEO_TEST_DATABASE_URL to a disposable PostgreSQL database before running tests")
    schema = "test_" + uuid.uuid4().hex
    monkeypatch.setenv("GEO_DATABASE_URL", dsn)
    monkeypatch.setenv("GEO_DATABASE_SCHEMA", schema)
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    from backend.store import get_snapshot
    get_snapshot.cache_clear()
    yield
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
    from backend.database import _initialized
    _initialized.discard((dsn, schema))
    get_snapshot.cache_clear()




@pytest.fixture(autouse=True)
def _legacy_qa_mode_for_existing_contract_tests(monkeypatch):
    """Old contract tests target the retained compatibility pipeline.

    Agent-specific tests call the new bounded runner directly or explicitly
    override this flag.  Production defaults to tools mode.
    """
    monkeypatch.setenv("LLM_AGENT_MODE", "legacy")
