from datetime import datetime, timezone

import pytest

from backend.qa_harness_state import HarnessScope
from backend.qa_plan_schema import ResearchTask, TaskBudget
from backend.qa_scope import ResearchScope
from backend.qa_tool_registry import ToolCapability, ToolCapabilityRegistry, default_registry


def scope(*, permissions=("public", "internal", "external_mcp")):
    value = ResearchScope.from_question(
        "最新美伊进展", view_scope=HarnessScope("snap-1", "usiran"),
        snapshot_created_at="2026-11-01T00:00:00+00:00",
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    return ResearchScope(**{**value.__dict__, "source_permissions": permissions})


def task(categories):
    return ResearchTask(
        "t1", "evidence", "核验近期军事与外交变化",
        allowed_tool_categories=list(categories), acceptance_criteria=["至少两条证据"],
        scope_topics=["usiran"], budget=TaskBudget(),
    )


def test_default_registry_contains_the_existing_nine_tools_without_duplicates():
    registry = default_registry()
    names = [item.name for item in registry.all()]
    assert len(names) == len(set(names)) == 9
    assert {
        "geo_workspace_summary", "geo_evidence_search", "geo_evidence_get",
        "geo_metric_catalog", "geo_metric_series", "geo_analysis_view",
        "geo_internal_message_search", "geo_internal_message_get",
        "geo_internal_message_stats",
    } == set(names)
    assert registry.schema_version.startswith("tools-")


def test_registry_filters_categories_and_internal_permissions():
    registry = default_registry()
    allowed = registry.select(task(["public_evidence", "internal_research"]), scope())
    assert "geo_evidence_search" in {item.name for item in allowed}
    assert "geo_internal_message_search" in {item.name for item in allowed}
    public_only = registry.select(
        task(["public_evidence", "internal_research"]), scope(permissions=("public",)),
    )
    assert "geo_internal_message_search" not in {item.name for item in public_only}


def test_registry_validates_tool_arguments_and_scope_before_execution():
    registry = default_registry()
    current_scope = scope()
    normalized = registry.validate_call(
        "geo_evidence_search", {"q": "Iran", "limit": 10, "topic": "usiran"},
        task(["public_evidence"]), current_scope,
    )
    assert normalized["limit"] == 10
    with pytest.raises(ValueError, match="参数"):
        registry.validate_call(
            "geo_evidence_search", {"q": "Iran", "limit": 100},
            task(["public_evidence"]), current_scope,
        )
    with pytest.raises(ValueError, match="主题"):
        registry.validate_call(
            "geo_evidence_search", {"q": "Ukraine", "topic": "ukraine"},
            task(["public_evidence"]), current_scope,
        )


def test_external_mcp_tool_needs_explicit_source_permission():
    registry = ToolCapabilityRegistry()
    registry.register(ToolCapability(
        name="external_news_search", category="public_evidence", description="外部新闻检索",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        source_type="external_mcp", mcp_server="news", data_classification="public",
    ))
    assert registry.select(task(["public_evidence"]), scope())
    assert not registry.select(
        task(["public_evidence"]), scope(permissions=("public", "internal")),
    )


def test_register_rejects_duplicate_or_mutating_tool():
    registry = ToolCapabilityRegistry()
    capability = ToolCapability(
        name="safe", category="public_evidence", description="只读",
        input_schema={"type": "object", "properties": {}},
    )
    registry.register(capability)
    with pytest.raises(ValueError, match="重复"):
        registry.register(capability)
    with pytest.raises(ValueError, match="只读"):
        registry.register(ToolCapability(
            name="write", category="public_evidence", description="修改",
            input_schema={"type": "object", "properties": {}}, read_only=False,
        ))
