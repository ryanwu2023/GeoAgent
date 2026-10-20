"""Shared polling data must survive the same scope filters as the dashboard."""
from copy import deepcopy

import pytest

from backend.research import metrics


POLL_SERIES = ("taco_trump_approval", "RCPPTAPP_approve")


def poll(series="taco_trump_approval", *, shared=True, topic=None):
    return {
        "id": ("finance:" if shared else "taco:") + series,
        "series": series,
        "name": "特朗普支持率（Silver Bulletin 聚合）",
        "unit": "%",
        "frequency": "日频",
        "scope": "共享市场背景" if shared else "跨资产市场背景",
        "method": "Silver Bulletin / Datawrapper 聚合值",
        "boundary": "聚合支持率，不是单次民调或冲突因果判断。",
        "source_ids": ["shared-poll" if shared else "taco-poll"],
        "history": [
            {"date": "2026-09-27", "value": 37.2},
            {"date": "2026-09-28", "value": 37.4},
            {"date": "2026-09-29", "value": 37.5},
        ],
        "shared_market": shared,
        "topic": topic,
        "dimension": "financial",
        "bucket": "taco",
    }


def snapshot(*observations):
    return {
        "created_at": "2026-09-30T06:37:44+00:00",
        "observations": list(observations),
    }


@pytest.mark.parametrize("series", POLL_SERIES)
def test_shared_poll_alone_is_visible_on_usiran(series):
    data = snapshot(poll(series))
    original = deepcopy(data)

    result = metrics(data, "usiran")

    assert len(result) == 1
    assert result[0]["series"] == series
    assert result[0]["current"] == {"date": "2026-09-29", "value": 37.5}
    assert result[0]["source_ids"] == ["shared-poll"]
    assert result[0]["freshness"] == "可用"
    assert data == original  # Projecting a dashboard never rewrites a snapshot.


@pytest.mark.parametrize("shared_first", [True, False])
def test_dual_poll_sources_keep_theme_bound_series_without_duplicates(shared_first):
    shared = poll()
    taco = poll("RCPPTAPP_approve", shared=False, topic="usiran")
    observations = [shared, taco] if shared_first else [taco, shared]
    data = snapshot(*observations)

    usiran = metrics(data, "usiran")

    assert len(usiran) == 1
    assert usiran[0]["id"] == taco["id"]
    assert usiran[0]["source_ids"] == ["taco-poll"]
    assert len(metrics(data, "all")) == 1
    assert metrics(data, "ukraine") == []


def test_two_shared_aliases_do_not_create_duplicate_poll_cards():
    data = snapshot(poll(), poll("RCPPTAPP_approve"))

    for topic in ("usiran", "all"):
        result = metrics(data, topic)
        assert len(result) == 1
        assert result[0]["series"] in POLL_SERIES


@pytest.mark.parametrize("series", POLL_SERIES)
def test_poll_scope_keeps_topic_and_dimension_boundaries(series):
    data = snapshot(poll(series))

    assert len(metrics(data, "all")) == 1
    assert len(metrics(data, "usiran", "financial")) == 1
    assert metrics(data, "ukraine") == []
    assert metrics(data, "usiran", "us_opinion") == []
    assert metrics(data, "usiran", "military") == []


def test_theme_bound_poll_alone_remains_visible():
    data = snapshot(poll("RCPPTAPP_approve", shared=False, topic="usiran"))

    assert len(metrics(data, "usiran", "financial")) == 1
    assert metrics(data, "ukraine") == []


def test_poll_history_and_comparison_respect_date_window():
    data = snapshot(poll())

    result = metrics(
        data, "usiran", "financial", start="2026-09-27", end="2026-09-28"
    )[0]

    assert result["history"] == data["observations"][0]["history"][:2]
    assert result["current"] == {"date": "2026-09-28", "value": 37.4}
    assert result["previous"] == {"date": "2026-09-27", "value": 37.2}
    assert result["delta"] == pytest.approx(0.2)
    assert result["delta_unit"] == "个百分点"
    assert result["pct"] is None


def test_single_day_poll_does_not_borrow_a_previous_value_outside_scope():
    data = snapshot(poll())

    result = metrics(
        data, "usiran", "financial", start="2026-09-28", end="2026-09-28"
    )[0]

    assert result["current"] == {"date": "2026-09-28", "value": 37.4}
    assert result["previous"] is None
    assert result["delta"] is None


def test_poll_without_points_in_date_window_does_not_leak_latest_value():
    data = snapshot(poll())

    result = metrics(
        data, "usiran", "financial", start="2026-10-01", end="2026-10-02"
    )[0]

    assert result["history"] == []
    assert result["current"] is None
    assert result["previous"] is None
    assert result["delta"] is None
    assert result["freshness"] == "无有效值"


@pytest.mark.parametrize("series", POLL_SERIES)
def test_poll_projection_replaces_only_generic_market_metadata(series):
    observation = poll(series)
    observation.update(
        method="来源公开观测；市场行情为已收盘值，非实时价格",
        scope="共享市场背景",
        boundary="同期变动不等于冲突因果；行情与官方统计存在发布滞后",
    )
    data = snapshot(observation)
    original = deepcopy(data)

    result = metrics(data, "usiran")[0]

    assert result["scope"] == "共享民意背景"
    assert result["method"] == "来源公开支持率观测；日频更新不等于每天开展独立调查"
    assert result["boundary"] == "须核对调查机构、调查期、样本与汇总方法；支持率变化不证明冲突因果"
    assert result["history"] == observation["history"]
    assert result["source_ids"] == observation["source_ids"]
    assert data == original


@pytest.mark.parametrize("series", POLL_SERIES)
def test_poll_projection_preserves_explicit_source_method_and_boundary(series):
    observation = poll(series)
    original = deepcopy(observation)

    result = metrics(snapshot(observation), "usiran")[0]

    for field in ("scope", "method", "boundary"):
        assert result[field] == original[field]
    assert observation == original


def test_poll_generic_method_does_not_overwrite_specific_scope_or_boundary():
    observation = poll()
    observation.update(
        method="来源公开观测；市场行情为已收盘值，非实时价格",
        scope="美国成年人",
        boundary="样本限制以该次调查说明为准。",
    )

    result = metrics(snapshot(observation), "usiran")[0]

    assert result["scope"] == observation["scope"]
    assert result["boundary"] == observation["boundary"]
    assert result["method"] == "来源公开支持率观测；日频更新不等于每天开展独立调查"


def test_market_metadata_is_unchanged_for_unrelated_series():
    observation = poll("brent")
    observation.update(
        method="来源公开观测；市场行情为已收盘值，非实时价格",
        scope="共享市场背景",
        boundary="同期变动不等于冲突因果；行情与官方统计存在发布滞后",
    )

    result = metrics(snapshot(observation), "usiran")[0]

    for field in ("scope", "method", "boundary"):
        assert result[field] == observation[field]
