import pytest

from backend.qa_harness_state import AgentTaskState, HarnessBudget, HarnessEvent, HarnessScope, ToolCallState
from backend.qa_harness_store import HarnessStore, ScopeMismatchError


def test_store_round_trip_and_scope_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    scope = HarnessScope("s1", "usiran", "", "2026-10-01", "2026-10-24")
    run = store.create_run(
        run_id="run-1", answer_id="answer-1", scope=scope, question="问题",
        plan={"focuses": ["外交"]}, budget=HarnessBudget(), model="reasoning-test",
    )
    store.append_step("run-1", HarnessEvent(1, "planning", "stage", "completed", summary="完成"))
    artifact_id = store.save_artifact("run-1", "call-1", {"records": ["中文\n内容"]}, {"records": 1}, 8)
    run.research_memory = {"facts": ["事实"]}
    store.save_checkpoint(run)

    recovered = HarnessStore().find_resumable_run("run-1", scope)
    assert recovered.research_memory == {
        "facts": ["事实"], "ledger": {"version": 1, "entries": []},
    }
    assert store.load_artifact(artifact_id) == {"records": ["中文\n内容"]}
    assert store.load_artifacts([artifact_id, "missing", artifact_id]) == {
        artifact_id: {"records": ["中文\n内容"]},
    }
    assert store.list_steps("run-1")[0]["summary"] == "完成"
    with pytest.raises(ScopeMismatchError):
        store.find_resumable_run("run-1", HarnessScope("s2", "usiran"))


def test_store_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    scope = HarnessScope("s1")
    store.create_run(
        run_id="run-secret", answer_id="a", scope=scope, question="问题",
        plan={"Authorization": "Bearer abc123", "nested": {"api_key": "secret"}},
        budget=HarnessBudget(), model="test",
    )
    run = store.load_run("run-secret")
    assert run.plan["Authorization"] == "[已脱敏]"
    assert run.plan["nested"]["api_key"] == "[已脱敏]"


def test_tool_call_detail_is_owned_by_run_and_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    run = store.create_run(
        run_id="run-detail", answer_id="a", scope=HarnessScope("s1"), question="问题",
        plan={}, budget=HarnessBudget(), model="test",
    )
    artifact_id = store.save_artifact(
        "run-detail", "call-detail",
        {"records": [{"id": "r1", "text": "正文"}], "path": r"D:\\private\\secret.txt", "Authorization": "Bearer secret"},
        {"records": [{"id": "r1"}], "truncated": True}, 40,
    )
    run.tool_calls["call-detail"] = ToolCallState(
        call_id="call-detail", tool="geo_evidence_search", arguments={"q": "Iran", "api_key": "secret"},
        status="completed", attempts=2, artifact_id=artifact_id, result_count=1, duration_ms=15,
        phase="discovering", started_at="2026-10-24T00:00:00+00:00",
        finished_at="2026-10-24T00:00:00.015000+00:00", arguments_summary={"q": "Iran", "api_key": "[已脱敏]"},
        result_summary="返回 1 条结果", truncated=True, evidence_ids=["r1"],
    )
    store.save_checkpoint(run)

    detail = store.get_tool_call_detail("run-detail", "call-detail")
    encoded = str(detail)
    assert detail["snapshot_id"] == "s1"
    assert detail["artifact"]["payload"]["records"][0]["id"] == "r1"
    assert detail["artifact"]["preview"]["truncated"] is True
    assert "secret" not in encoded
    assert r"D:\\private" not in encoded
    assert store.get_tool_call_detail("run-detail", "missing") is None
    assert store.get_tool_call_detail("missing", "call-detail") is None

    other = store.create_run(
        run_id="run-other", answer_id="b", scope=HarnessScope("s1"), question="问题",
        plan={}, budget=HarnessBudget(), model="test",
    )
    assert store.get_tool_call_detail(other.run_id, "call-detail") is None


def test_specialist_tool_detail_requires_matching_task_and_agent_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    run = store.create_run(
        run_id="run-agent-detail", answer_id="a", scope=HarnessScope("s1"), question="问题",
        plan={}, budget=HarnessBudget(), model="test",
    )
    artifact_id = store.save_artifact("run-agent-detail", "event-call", {"records": []}, {"records": []}, 2)
    run.tool_calls["event-call"] = ToolCallState(
        call_id="event-call", tool="geo_evidence_search", status="completed",
        artifact_id=artifact_id, task_id="event-1", agent_id="event-agent", agent_role="event",
        parent_agent_id="coordinator-agent",
    )
    run.agent_tasks["event-1"] = AgentTaskState(
        task_id="event-1", agent_id="event-agent", agent_role="event",
        status="completed", tool_call_ids=["event-call"],
    )
    store.save_checkpoint(run)
    detail = store.get_tool_call_detail("run-agent-detail", "event-call")
    assert detail["task_id"] == "event-1"
    assert detail["agent_id"] == "event-agent"

    run.agent_tasks["event-1"].agent_id = "forged-agent"
    store.save_checkpoint(run)
    assert store.get_tool_call_detail("run-agent-detail", "event-call") is None


def test_store_restores_research_ledger_and_backfills_legacy_empty_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    run = store.create_run(
        run_id="run-ledger", answer_id="a", scope=HarnessScope("s1"), question="问题",
        plan={}, budget=HarnessBudget(), model="test",
    )
    run.research_memory = {"ledger": {"version": 1, "entries": [{
        "ledger_id": "l1", "facet": "diplomacy", "claim": "存在接触",
        "claim_type": "media_report", "status": "reported",
    }]}}
    store.save_checkpoint(run)
    restored = store.load_run("run-ledger")
    assert restored.research_memory["ledger"]["entries"][0]["claim"] == "存在接触"

    legacy = store.create_run(
        run_id="run-legacy", answer_id="b", scope=HarnessScope("s1"), question="问题",
        plan={}, budget=HarnessBudget(), model="test",
    )
    legacy.research_memory = {"facts": ["旧数据"]}
    store.save_checkpoint(legacy)
    recovered = store.load_run("run-legacy")
    assert recovered.research_memory["facts"] == ["旧数据"]
    assert recovered.research_memory["ledger"] == {"version": 1, "entries": []}
