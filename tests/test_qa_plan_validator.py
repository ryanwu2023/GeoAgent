from dataclasses import replace
from datetime import datetime, timezone

import pytest

from backend.qa_harness_state import HarnessScope
from backend.qa_plan_schema import QaPolicy, ResearchGraph, ResearchTask, TaskBudget, TaskStatus
from backend.qa_plan_validator import validate_graph
from backend.qa_scope import ResearchScope


def scope() -> ResearchScope:
    return ResearchScope.from_question(
        "最新美伊局势", view_scope=HarnessScope("s1", "usiran"),
        snapshot_created_at="2026-10-31T01:00:00Z",
        now=datetime(2026, 10, 31, 2, 0, tzinfo=timezone.utc),
    )


def task(task_id: str, *, depends=None, categories=None, topics=None, calls=2, seconds=20):
    return ResearchTask(
        task_id=task_id, task_type="evidence_discovery", objective=f"执行{task_id}",
        depends_on=list(depends or []),
        allowed_tool_categories=list(categories or ["public_evidence"]),
        acceptance_criteria=["取得证据"], budget=TaskBudget(tool_calls=calls, seconds=seconds),
        scope_topics=list(topics or ["usiran"]),
    )


def graph(tasks, **kwargs):
    return ResearchGraph.create(
        question="最新美伊局势", research_objective="确认变化", mode="standard",
        scope=scope(), tasks=tasks, **kwargs,
    )


@pytest.mark.parametrize("tasks,error", [
    ([task("T1", depends=["missing"])], "unknown_dependency"),
    ([task("T1", depends=["T2"]), task("T2", depends=["T1"])], "dependency_cycle"),
    ([task("T1", categories=["unknown_tool_kind"])], "unknown_tool_category"),
    ([task("T1", topics=["ukraine"])], "topic_out_of_scope"),
])
def test_validator_rejects_invalid_dependencies_capabilities_and_scope(tasks, error):
    result = validate_graph(graph(tasks), QaPolicy())
    assert result.valid is False
    assert any(error in item for item in result.errors)


def test_validator_rejects_active_task_and_budget_limits():
    too_many = graph([task(f"T{index}") for index in range(1, 4)])
    result = validate_graph(too_many, QaPolicy(max_tasks=2, max_parallel_tasks=2))
    assert any("active_task_limit" in item for item in result.errors)

    per_task = validate_graph(graph([task("T1", calls=9)]), QaPolicy())
    assert any("task_tool_budget" in item for item in per_task.errors)

    cumulative = graph([task("T1", calls=4)], cumulative_tool_calls=46)
    result = validate_graph(cumulative, QaPolicy(max_total_tool_calls=48))
    assert any("cumulative_tool_budget" in item for item in result.errors)


def test_validator_rejects_revision_and_deadline_reset():
    invalid_revision = graph([task("T1")], revision=3, parent_revision=1)
    result = validate_graph(invalid_revision, QaPolicy())
    assert any("revision_chain" in item for item in result.errors)

    wrong_deadline = graph([task("T1")])
    wrong_deadline.deadline_at = "2026-11-01T00:00:00+00:00"
    result = validate_graph(wrong_deadline, QaPolicy())
    assert any("deadline_mismatch" in item for item in result.errors)


def test_valid_graph_passes_and_reports_roots():
    value = graph([task("T1"), task("T2", depends=["T1"])])
    result = validate_graph(value, QaPolicy())
    assert result.valid is True
    assert result.errors == []
    assert result.roots == ["T1"]


def test_validator_enforces_source_permission_and_does_not_rebudget_completed_tasks():
    restricted = replace(scope(), source_permissions=("public",))
    denied = ResearchGraph.create(
        question="最新美伊局势", research_objective="确认变化", mode="standard",
        scope=restricted,
        tasks=[
            task("T1", categories=["internal_research"]),
            task("T2", depends=["T1"]),
        ],
    )
    assert any("source_permission_denied" in item for item in validate_graph(denied, QaPolicy()).errors)

    completed = task("T1", calls=8)
    completed.status = TaskStatus.COMPLETED
    current = task("T2", depends=["T1"], calls=2)
    value = ResearchGraph.create(
        question="最新美伊局势", research_objective="确认变化", mode="standard",
        scope=scope(), tasks=[completed, current], cumulative_tool_calls=40,
    )
    assert validate_graph(value, QaPolicy(max_total_tool_calls=48)).valid is True
