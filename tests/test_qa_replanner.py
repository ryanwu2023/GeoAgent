from datetime import datetime, timezone

from backend.qa_harness_state import HarnessScope
from backend.qa_plan_schema import (
    ProgressFingerprint, QaPolicy, ResearchGraph, ResearchTask, TaskBudget,
    TaskEvaluation, TaskStatus,
)
from backend.qa_replanner import replan
from backend.qa_plan_validator import validate_graph
from backend.qa_scope import ResearchScope


def graph(*, no_progress=0, progress_keys=()):
    scope = ResearchScope.from_question(
        "最新美伊进展", view_scope=HarnessScope("s1", "usiran"),
        snapshot_created_at="2026-11-01T00:00:00Z",
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    task = ResearchTask(
        "T1", "event_assessment", "核验事件", allowed_tool_categories=["public_evidence"],
        acceptance_criteria=["核验关键行动"], scope_topics=["usiran"],
        budget=TaskBudget(tool_calls=2, seconds=20, context_tokens=4000),
        status=TaskStatus.COMPLETED,
    )
    return ResearchGraph.create(
        question="最新美伊进展", research_objective="核验", mode="standard",
        scope=scope, tasks=[task], no_progress_rounds=no_progress,
        progress_fingerprint_keys=list(progress_keys),
    )


def evaluation(*, result="incomplete", action="source_verification", gap="缺少独立来源"):
    return TaskEvaluation(
        task_id="T1", criterion_id="all", result=result,
        gaps=[gap] if gap else [], suggested_action=action,
    )


def progress(*, origins=("wire-1",), full=("e1",)):
    return ProgressFingerprint(
        independent_origin_groups=list(origins), full_text_ids=list(full), claim_ids=["c1"],
    )


def test_replan_adds_only_targeted_gap_task_and_records_reason():
    updated = replan(graph(), [evaluation()], progress(), QaPolicy())
    assert updated is not None and updated.revision == 2
    assert updated.parent_revision == 1
    assert len(updated.tasks) == 2
    added = updated.tasks[-1]
    assert added.task_type == "source_verification"
    assert added.depends_on == ["T1"]
    assert "缺少独立来源" in added.objective
    assert updated.revision_reason.startswith("补查")
    assert validate_graph(updated, QaPolicy()).valid is True


def test_same_syndicated_results_do_not_count_as_progress_and_stop_after_two_rounds():
    current = progress(origins=("same-wire",), full=("same-copy",))
    existing = graph(no_progress=1, progress_keys=[current.stable_key()])
    assert replan(existing, [evaluation()], current, QaPolicy(max_no_progress_rounds=2)) is None


def test_satisfied_tasks_do_not_generate_redundant_follow_up():
    assert replan(
        graph(), [evaluation(result="satisfied", action="", gap="")], progress(), QaPolicy(),
    ) is None


def test_metric_narrative_conflict_creates_metric_verification_task():
    updated = replan(
        graph(), [evaluation(result="conflicted", action="metric_conflict_verification", gap="指标与叙事冲突")],
        progress(), QaPolicy(),
    )
    assert updated is not None
    added = updated.tasks[-1]
    assert "market_data" in added.allowed_tool_categories
    assert "冲突" in added.objective


def test_unresolved_conflict_evidence_is_not_removed_from_graph():
    existing = graph()
    existing.claim_ids = ["claim-support", "claim-counter"]
    updated = replan(
        existing,
        [TaskEvaluation(
            "T1", "all", "conflicted", claim_ids=["claim-counter"],
            evidence_ids=["denial-1"], conflicts=["双方口径冲突"],
            suggested_action="source_verification",
        )],
        progress(), QaPolicy(),
    )
    assert updated is not None
    assert updated.claim_ids == ["claim-support", "claim-counter"]

