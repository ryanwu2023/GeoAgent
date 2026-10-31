from datetime import datetime, timezone

from backend.qa_harness_state import HarnessScope
from backend.qa_plan_schema import QaPolicy
from backend.qa_plan_validator import validate_graph
from backend.qa_planner import build_research_graph
from backend.qa_router import classify_task
from backend.qa_scope import ResearchScope


def scope(question: str) -> ResearchScope:
    topic = "ukraine" if "俄乌" in question else "usiran"
    return ResearchScope.from_question(
        question, view_scope=HarnessScope("s1", topic),
        snapshot_created_at="2026-10-31T01:00:00Z",
        now=datetime(2026, 10, 31, 2, 0, tzinfo=timezone.utc),
    )


def test_planner_builds_small_fast_graph_and_deeper_research_graph():
    fast_question = "请给出布伦特原油最新值"
    fast_scope = scope(fast_question)
    fast_profile = classify_task(fast_question, fast_scope, model_result=None)
    fast = build_research_graph(fast_question, fast_profile, fast_scope, QaPolicy())
    assert fast.mode == "fast"
    assert 1 <= len(fast.tasks) <= 2
    assert validate_graph(fast, QaPolicy()).valid is True

    deep_question = "伊朗会封锁霍尔木兹海峡吗，对原油和美债有什么影响？"
    deep_scope = scope(deep_question)
    deep_profile = classify_task(deep_question, deep_scope, model_result=None)
    deep = build_research_graph(deep_question, deep_profile, deep_scope, QaPolicy())
    assert deep.mode == "research"
    assert 4 <= len(deep.tasks) <= 8
    assert any(task.task_type == "counterevidence" for task in deep.tasks)
    assert any("market_data" in task.allowed_tool_categories for task in deep.tasks)
    assert validate_graph(deep, QaPolicy()).valid is True


def test_invalid_model_graph_is_ignored_in_favor_of_valid_deterministic_graph():
    question = "最新俄乌局势"
    research_scope = scope(question)
    profile = classify_task(question, research_scope, model_result=None)
    graph = build_research_graph(
        question, profile, research_scope, QaPolicy(),
        model_graph={"tasks": [{"task_id": "bad", "depends_on": ["missing"]}]},
    )
    assert graph.basis == "deterministic_fallback"
    assert validate_graph(graph, QaPolicy()).valid is True
