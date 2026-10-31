from pathlib import Path
import re


SCHEMA = (Path(__file__).resolve().parents[1] / "backend" / "schema.sql").read_text(encoding="utf-8")


def _table(name: str) -> str:
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {re.escape(name)}\s*\((.*?)\);",
        SCHEMA,
        flags=re.S | re.I,
    )
    assert match is not None, f"缺少表 {name}"
    return match.group(1)


def test_graph_persistence_tables_have_stable_primary_keys():
    assert "run_id TEXT PRIMARY KEY" in _table("qa_graph_runs")
    assert "PRIMARY KEY(run_id,revision,task_id)" in _table("qa_graph_nodes")
    assert "attempt_id TEXT PRIMARY KEY" in _table("qa_graph_attempts")
    assert "PRIMARY KEY(run_id,sequence)" in _table("qa_graph_events")


def test_graph_run_and_attempt_tables_keep_recovery_fields():
    run_table = _table("qa_graph_runs")
    for column in (
        "scope_json", "config_version", "deadline_at", "plan_json", "cost_json",
        "status", "result_json", "tool_schema_version",
    ):
        assert column in run_table
    attempt_table = _table("qa_graph_attempts")
    for column in (
        "execution_lease", "dedupe_key", "arguments_json", "status", "artifact_id",
        "error_json", "cache_reused",
    ):
        assert column in attempt_table
