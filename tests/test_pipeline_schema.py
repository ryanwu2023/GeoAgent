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


def test_runs_trigger_check_batch_trial():
    with db() as conn:
        _insert(conn, "p1", "管线")
        sql = ("INSERT INTO collection_pipeline_runs(run_id,pipeline_id,version,trigger,status,started_at,data_timestamp) "
               "VALUES (%s,'p1',1,%s,'running',now(),now())")
        for trigger in ("batch", "trial"):
            conn.execute(sql, (f"r-{trigger}", trigger))
        for trigger in ("schedule", "manual", "mcp"):
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(sql, (f"r-{trigger}", trigger))


def test_pipelines_have_no_schedule_columns_or_due_index():
    with db() as conn:
        cols = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
            "AND table_name='collection_pipelines'")}
        idx = {r["indexname"] for r in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND tablename='collection_pipelines'")}
    assert not cols & {"interval_sec", "daily_times", "timezone", "next_run_at"}
    assert "collection_pipelines_due" not in idx


def test_owner_comment_is_creator_record_only():
    with db() as conn:
        comment = conn.execute(
            "SELECT col_description('collection_pipelines'::regclass, ordinal_position) AS c FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='collection_pipelines' AND column_name='owner'").fetchone()["c"]
    assert comment == "创建该管线的管理员账号，仅作记录"
