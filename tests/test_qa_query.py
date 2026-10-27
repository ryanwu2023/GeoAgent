from datetime import date

from backend.qa_query import (
    FacetQuery,
    classify_topic_lane,
    compile_query,
    freshness_metadata,
    freshness_windows,
)
from backend.qa_retrieval import hybrid_retrieve


def item(rid, title, text, published_at, *, event_at=None, tier="T2"):
    return {
        "id": rid,
        "title": title,
        "text": text,
        "published_at": published_at,
        "event_at": event_at,
        "source_id": rid,
        "source_name": rid,
        "tier": tier,
        "verification": "pending",
    }


def test_topic_lane_requires_direct_topic_and_action_for_core():
    ukraine = FacetQuery.for_topic("ukraine", "俄乌最新情况", facet="military")
    direct = item("u", "基辅遭无人机袭击", "乌克兰防空部队拦截俄罗斯无人机。", "2026-10-25T00:00:00Z")
    iran = item("i", "俄罗斯提出斡旋伊朗问题", "俄罗斯外长讨论伊朗核谈判。", "2026-10-25T01:00:00Z")
    background = item("b", "俄罗斯市场波动", "俄罗斯股市上涨，但未提及战争。", "2026-10-25T02:00:00Z")
    assert classify_topic_lane(direct, ukraine) == "core"
    assert classify_topic_lane(iran, ukraine) == "rejected"
    assert classify_topic_lane(background, ukraine) == "cross_topic"


def test_usiran_generic_middle_east_oil_is_cross_topic_not_core():
    query = FacetQuery.for_topic("usiran", "最新美伊局势", facet="event")
    generic = item("oil", "中东原油风险升温", "海湾供应风险推动油价上涨。", "2026-10-25T00:00:00Z")
    direct = item("iran", "伊朗回应美国制裁", "伊朗外交部回应美国新制裁。", "2026-10-25T01:00:00Z")
    assert classify_topic_lane(generic, query) == "cross_topic"
    assert classify_topic_lane(direct, query) == "core"


def test_query_spec_filters_excluded_entities_before_ranking():
    query = FacetQuery(
        facet="military", query="俄乌前线最新变化",
        must_entities=("乌克兰", "俄罗斯"), should_terms=("战线", "无人机"),
        exclude_entities=("伊朗",), start="2026-10-20", end="2026-10-25",
        preferred_tiers=("T1", "T2"), expected_role="support", lane="core", topic="ukraine",
    )
    records = [
        item("front", "俄乌前线变化", "俄罗斯与乌克兰在战线交火。", "2026-10-24T00:00:00Z"),
        item("polluted", "俄罗斯与伊朗合作", "俄罗斯向伊朗提供装备。", "2026-10-25T00:00:00Z"),
    ]
    result = hybrid_retrieve(records, query.query, limit=5, query_spec=query)
    assert [row["id"] for row in result] == ["front"]


def test_compile_query_keeps_public_tool_contract():
    query = FacetQuery.for_topic("usiran", "美伊外交谈判", facet="diplomacy")
    payload = compile_query(query, limit=8)
    assert set(payload) <= {"q", "limit", "source_tiers"}
    assert payload["q"] == "美伊外交谈判"
    assert payload["limit"] == 8


def test_freshness_uses_event_time_and_reports_stale_high_confidence():
    rows = [
        item("reprint", "旧事件今日转载", "伊朗旧闻。", "2026-10-25T00:00:00Z", event_at="2026-10-10T00:00:00Z", tier="T2"),
        item("low", "今日传闻", "社交媒体传闻。", "2026-10-25T01:00:00Z", tier="T3"),
    ]
    meta = freshness_metadata(rows, as_of="2026-10-25T04:00:00Z")
    assert meta["latest_any_source_at"].startswith("2026-10-25")
    assert meta["latest_high_confidence_at"].startswith("2026-10-10")
    assert "72" in meta["stale_warning"]


def test_latest_windows_expand_from_48_hours_to_background():
    windows = freshness_windows("最新美伊局势", date(2026, 10, 25))
    assert [item["name"] for item in windows] == ["latest_48h", "latest_7d", "background_30d"]
    assert windows[0]["start"] == "2026-10-23"
    assert windows[1]["start"] == "2026-10-19"
    assert windows[2]["start"] == "2026-09-25"

