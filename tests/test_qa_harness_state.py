import pytest

from backend.qa_harness_state import (
    HarnessEvent, HarnessPhase, HarnessRun, HarnessScope, InvalidHarnessTransition,
    ToolCallState, transition,
)


def test_scope_hash_is_stable_and_includes_all_filters():
    base = HarnessScope("s1", "usiran", "diplomacy", "2026-10-01", "2026-10-24")
    assert base.stable_hash() == HarnessScope("s1", "usiran", "diplomacy", "2026-10-01", "2026-10-24").stable_hash()
    assert base.stable_hash() != HarnessScope("s2", "usiran", "diplomacy", "2026-10-01", "2026-10-24").stable_hash()
    assert base.stable_hash() != HarnessScope("s1", "ukraine", "diplomacy", "2026-10-01", "2026-10-24").stable_hash()


def test_only_declared_state_transitions_are_allowed():
    run = HarnessRun("run-1", "answer-1", "问题", HarnessScope("s1"))
    for phase in (
        HarnessPhase.DISCOVERING, HarnessPhase.READING, HarnessPhase.REVIEWING,
        HarnessPhase.SYNTHESIZING, HarnessPhase.VALIDATING, HarnessPhase.COMPLETED,
    ):
        transition(run, phase)
    assert run.status == "completed"
    with pytest.raises(InvalidHarnessTransition):
        transition(run, HarnessPhase.REVIEWING)


def test_degraded_is_available_from_active_phase():
    run = HarnessRun("run-1", "answer-1", "问题", HarnessScope("s1"))
    transition(run, HarnessPhase.DEGRADED)
    assert run.status == "degraded"


def test_tool_trace_v1_fields_have_safe_defaults_and_old_runs_still_load():
    event = HarnessEvent(1, "discovering", "tool", "running", call_id="c1", tool="geo_evidence_search")
    assert event.event_version == "tool-trace/v1"
    assert event.mcp_server == "geoagent"
    assert event.attempt == 1
    assert event.arguments_summary == {}
    assert event.error is None

    run = HarnessRun.from_dict(HarnessRun("run-old", "a", "q", HarnessScope("s1")).to_dict())
    run.tool_calls["c1"] = ToolCallState("c1", "geo_evidence_search")
    restored = HarnessRun.from_dict(run.to_dict())
    assert restored.tool_calls["c1"].mcp_server == "geoagent"
    assert restored.tool_calls["c1"].evidence_ids == []
