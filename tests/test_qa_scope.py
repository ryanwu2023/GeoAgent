from datetime import datetime, timezone

import pytest

from backend.qa_harness_state import HarnessScope
from backend.qa_scope import ResearchScope


NOW = datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc)


def test_explicit_usiran_question_overrides_ukraine_dashboard():
    scope = ResearchScope.from_question(
        "最新美伊局势", view_scope=HarnessScope("s1", "ukraine", "military"),
        snapshot_created_at="2026-10-30T22:00:00Z", now=NOW,
    )
    assert scope.topics == ("usiran",)
    assert scope.primary_topic == "usiran"
    assert scope.dimension == ""
    assert scope.topic_source == "user_explicit"


def test_cross_topic_question_keeps_both_topics():
    scope = ResearchScope.from_question(
        "比较美伊与俄乌对原油的共同影响", view_scope=HarnessScope("s1", "usiran"),
        snapshot_created_at="2026-10-30T22:00:00Z", now=NOW,
    )
    assert scope.topics == ("usiran", "ukraine")
    assert scope.primary_topic == "all"


def test_latest_window_uses_earlier_of_run_time_and_snapshot_time():
    scope = ResearchScope.from_question(
        "最新俄乌局势", view_scope=HarnessScope("s1", "ukraine"),
        snapshot_created_at="2026-10-25T22:00:00Z", now=NOW,
    )
    assert scope.data_as_of == "2026-10-25"
    assert scope.recent_start == "2026-10-19"
    assert scope.run_started_at.startswith("2026-10-31")
    assert scope.deadline_at > scope.run_started_at


def test_tool_arguments_cannot_silently_switch_topic_or_future_date():
    scope = ResearchScope.from_question(
        "最新美伊局势", view_scope=HarnessScope("s1", "ukraine"),
        snapshot_created_at="2026-10-30T22:00:00Z", now=NOW,
    )
    with pytest.raises(ValueError, match="主题范围"):
        scope.validate_arguments("geo_evidence_search", {"topic": "ukraine", "q": "俄乌"})
    with pytest.raises(ValueError, match="资料截至时间"):
        scope.validate_arguments("geo_evidence_search", {"end": "2026-10-31"})


def test_cache_scope_contains_scope_version_and_is_stable():
    first = ResearchScope.from_question(
        "最新美伊局势", view_scope=HarnessScope("s1", "usiran"),
        snapshot_created_at="2026-10-30T22:00:00Z", now=NOW,
    )
    second = ResearchScope.from_question(
        "最新美伊局势", view_scope=HarnessScope("s1", "usiran"),
        snapshot_created_at="2026-10-30T22:00:00Z", now=NOW,
    )
    assert first.cache_scope()["version"] == "research-scope/v1"
    assert first.stable_hash() == second.stable_hash()


def test_explicit_one_year_request_expands_background_window():
    scope = ResearchScope.from_question(
        "分析近12个月俄乌主线", view_scope=HarnessScope("s1", "ukraine"),
        snapshot_created_at="2026-10-30T22:00:00Z", now=NOW,
    )
    assert scope.background_start == "2025-11-05"
    assert scope.recent_start == "2025-11-05"
