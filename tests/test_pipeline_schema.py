import psycopg
import pytest

from backend.store import db

TABLES = ["collection_pipelines", "collection_pipeline_versions", "collection_pipeline_runs",
          "pipeline_seen_items", "collection_pipeline_run_items"]
COMMON = {"created_by": "YES", "created_at": "NO", "updated_by": "YES",
          "updated_at": "YES", "data_timestamp": "NO", "is_deleted": "NO"}


def test_pipeline_tables_have_common_fields_and_comments():
    with db() as conn:
        for table in TABLES:
            cols = {r["column_name"]: r["is_nullable"] for r in conn.execute(
                "SELECT column_name,is_nullable FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (table,))}
            assert {k: cols[k] for k in COMMON} == COMMON
            missing = conn.execute(
                "SELECT a.attname FROM pg_attribute a WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped AND col_description(a.attrelid,a.attnum) IS NULL", (table,)).fetchall()
            assert missing == []
            assert conn.execute("SELECT obj_description(%s::regclass)", (table,)).fetchone()["obj_description"]


def _insert(conn, pid, name):
    conn.execute(
        "INSERT INTO collection_pipelines(id,kind,name,topics,current_version,owner) VALUES (%s,'configurable',%s,ARRAY['usiran'],1,'admin')",
        (pid, name))


def test_active_name_unique_allows_reuse_after_delete():
    with db() as conn:
        _insert(conn, "p1", "同名管线")
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                _insert(conn, "p2", "同名管线")
        conn.execute("UPDATE collection_pipelines SET is_deleted=true WHERE id='p1'")
        _insert(conn, "p2", "同名管线")
        n = conn.execute("SELECT count(*) AS n FROM collection_pipelines WHERE name='同名管线' AND NOT is_deleted").fetchone()["n"]
        assert n == 1


def test_run_items_summary_column_has_comment():
    with db() as conn:
        col = conn.execute(
            "SELECT is_nullable,column_default,col_description('collection_pipeline_run_items'::regclass,ordinal_position) AS c "
            "FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='collection_pipeline_run_items' "
            "AND column_name='summary'").fetchone()
    assert col and col["is_nullable"] == "NO" and "''" in col["column_default"] and col["c"]
