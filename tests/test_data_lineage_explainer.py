import json

from scripts import build_data_lineage_explainer as explainer


def test_every_enabled_collector_has_business_scope() -> None:
    registry = json.loads(explainer.REGISTRY.read_text(encoding="utf-8-sig"))
    enabled_ids = {
        item["id"]
        for item in registry.get("collectors", [])
        if item.get("enabled", True)
    }

    assert len(enabled_ids) == 28
    assert enabled_ids == set(explainer.COLLECTOR_RESEARCH_SCOPE)
    for collector_id in enabled_ids:
        item = explainer.COLLECTOR_RESEARCH_SCOPE[collector_id]
        assert item["dimensions"], collector_id
        assert item["scope"].strip(), collector_id


def test_research_framework_keeps_eight_plus_five_dimensions() -> None:
    assert len(explainer.RESEARCH_FRAMEWORK) == 13
    assert sum(topic == "美伊局势" for topic, _, _ in explainer.RESEARCH_FRAMEWORK) == 8
    assert sum(topic == "俄乌冲突" for topic, _, _ in explainer.RESEARCH_FRAMEWORK) == 5
    assert len({(topic, dimension) for topic, dimension, _ in explainer.RESEARCH_FRAMEWORK}) == 13


def test_future_work_covers_configurable_topics_sources_mcp_and_daily_reports() -> None:
    rendered = explainer.render(explainer.build_dataset(explainer.latest_snapshot()))

    assert "地缘主题配置化" in rendered
    assert "法国内乱" in rendered
    assert "关键词配置与采集 MCP 化" in rendered
    assert "华泰宏观新闻群" in rendered
    assert "自动生成日频报告" in rendered

