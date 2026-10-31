from dataclasses import asdict

import pytest

from backend.qa_plan_schema import (
    ExecutionMode,
    ProgressFingerprint,
    QaPolicy,
    ResearchGraph,
    ResearchTask,
    TaskBudget,
    TaskEvaluation,
    TaskProfile,
    TaskStatus,
)
from backend.qa_scope import ResearchScope
from backend.qa_harness_state import HarnessScope
from datetime import datetime, timezone


def scope() -> ResearchScope:
    return ResearchScope.from_question(
        "最新美伊局势", view_scope=HarnessScope("s1", "usiran"),
        snapshot_created_at="2026-10-31T01:00:00Z",
        now=datetime(2026, 10, 31, 2, 0, tzinfo=timezone.utc),
    )


def test_policy_defaults_match_quality_first_design_and_validate_ranges():
    policy = QaPolicy()
    assert policy.default_mode == "auto"
    assert policy.quality_profile == "quality_first"
    assert policy.deadline_seconds == 300
    assert policy.reserve_synthesis_seconds == 40
    assert policy.max_plan_revisions == 3
    assert policy.max_tasks == 8
    assert policy.max_parallel_tasks == 3
    assert policy.max_total_tool_calls == 48
    assert policy.max_tool_calls_per_task == 8
    assert policy.max_context_tokens == 64000
    assert policy.synthesis_reserve_output_tokens == 6000

    with pytest.raises(ValueError, match="reserve_synthesis_seconds"):
        QaPolicy(deadline_seconds=30, reserve_synthesis_seconds=40)
    with pytest.raises(ValueError, match="max_parallel_tasks"):
        QaPolicy(max_tasks=2, max_parallel_tasks=3)


def test_schema_round_trip_preserves_scope_tasks_and_budget():
    research_scope = scope()
    task = ResearchTask(
        task_id="T1", task_type="evidence_discovery", objective="检索最新证据",
        allowed_tool_categories=["public_evidence"], acceptance_criteria=["至少一条可读证据"],
        budget=TaskBudget(tool_calls=4, seconds=30), scope_topics=["usiran"],
    )
    graph = ResearchGraph.create(
        question="最新美伊局势", research_objective="确认近期变化",
        mode=ExecutionMode.RESEARCH, scope=research_scope, tasks=[task],
        config_version="policy-v1", model_role_version="roles-v1",
    )
    restored = ResearchGraph.from_dict(graph.to_dict())
    assert restored.to_dict() == graph.to_dict()
    assert restored.tasks[0].status == TaskStatus.PENDING


def test_evaluation_and_progress_contracts_are_json_safe():
    evaluation = TaskEvaluation(
        task_id="T1", criterion_id="C1", result="incomplete",
        claim_ids=["claim-1"], evidence_ids=["e1"], span_refs=["e1:0-10:v1"],
        gaps=["缺官方确认"], conflicts=["当事方否认"], suggested_action="补查官方来源",
    )
    progress = ProgressFingerprint(
        independent_origin_groups=["wire-1"], full_text_ids=["e1"],
        claim_ids=["claim-1"], resolved_conflict_ids=["conflict-1"],
        conclusion_boundaries=["不能确认行动已发生"],
    )
    assert asdict(evaluation)["span_refs"] == ["e1:0-10:v1"]
    assert progress.stable_key().startswith("progress-")


def test_task_profile_rejects_unknown_mode_and_invalid_confidence():
    with pytest.raises(ValueError, match="suggested_mode"):
        TaskProfile(
            intent="fact_lookup", breadth="narrow", depth="shallow", freshness="current",
            risk_level="low", suggested_mode="instant", confidence=0.9,
        )
    with pytest.raises(ValueError, match="confidence"):
        TaskProfile(
            intent="fact_lookup", breadth="narrow", depth="shallow", freshness="current",
            risk_level="low", suggested_mode="fast", confidence=1.5,
        )
