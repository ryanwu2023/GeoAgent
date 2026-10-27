import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace

from backend.qa_agent import LocalGeoTools, normalize_final, run_agent
from backend.qa_planner import build_question_plan, deterministic_plan
from backend.qa_retrieval import evidence_budget, hybrid_retrieve
from backend.research import validate_citation_mapped_markdown


def record(rid, title, text, date, source, tier="T2"):
    return {
        "id": rid,
        "title": title,
        "text": text,
        "published_at": date,
        "collected_at": date,
        "source_id": source,
        "source_name": source,
        "tier": tier,
        "verification": "pending",
        "scope": "event",
        "topics": ["usiran"],
        "dimensions": {"usiran": "diplomacy"},
        "dimension_candidates": {"usiran": ["diplomacy"]},
        "url": f"https://example.test/{rid}",
    }


def test_normalize_final_does_not_bind_all_full_text_to_synthetic_s1():
    tools = SimpleNamespace(retrieved_ids=["r1", "r2"], full_text_ids=["r1", "r2"])
    result = normalize_final({
        "answer_markdown": "## 结论\n\n当前仍需继续观察。",
        "citation_map": [],
        "gaps": [],
    }, tools)
    assert result["text"] == "## 结论\n\n当前仍需继续观察。"
    assert result["citation_map"] == []
    assert result["evidence_ids"] == []
    assert any("逐主张引用" in gap for gap in result["gaps"])


def test_normalize_final_keeps_only_full_text_ids_and_referenced_anchors():
    tools = SimpleNamespace(retrieved_ids=["r1", "r2", "r3"], full_text_ids=["r1", "r2"])
    result = normalize_final({
        "answer_markdown": "官方仍未确认行动〔S1〕。另有背景判断。",
        "citation_map": [
            {"anchor": "S1", "evidence_ids": ["r1", "r3"]},
            {"anchor": "S2", "evidence_ids": ["r2"]},
        ],
        "gaps": [],
    }, tools)
    assert result["citation_map"] == [{"anchor": "S1", "evidence_ids": ["r1"]}]
    assert result["evidence_ids"] == ["r1"]
    assert "〔S1〕" not in result["text"]
    assert "〔S1〕" in result["annotated_text"]
    assert any("部分引用" in gap for gap in result["gaps"])


def test_normalize_final_repairs_visible_evidence_id_lines_into_hidden_anchors():
    tools = SimpleNamespace(retrieved_ids=["r1"], full_text_ids=["r1"])
    result = normalize_final({
        "answer_markdown": "官方仍未确认行动。\n- 证据ID：S1（r1）",
        "citation_map": [{"anchor": "S1", "evidence_ids": ["r1"]}],
        "gaps": [],
    }, tools)
    assert result["citation_map"] == [{"anchor": "S1", "evidence_ids": ["r1"]}]
    assert "证据ID" not in result["text"]
    assert "r1" not in result["text"]
    assert "〔S1〕" in result["annotated_text"]


def test_normalize_final_maps_raw_record_id_notation_back_to_citation_anchor():
    tools = SimpleNamespace(retrieved_ids=["record-123"], full_text_ids=["record-123"])
    result = normalize_final({
        "answer_markdown": "伊方作出公开表态（证据ID：record-123）。\n- **证据ID**：record-123",
        "citation_map": [{"anchor": "S4", "evidence_ids": ["record-123"]}],
        "gaps": [],
    }, tools)
    assert result["citation_map"] == [{"anchor": "S4", "evidence_ids": ["record-123"]}]
    assert "record-123" not in result["text"]
    assert "〔S4〕" in result["annotated_text"]


def test_normalize_final_preserves_non_citation_bracketed_labels():
    tools = SimpleNamespace(full_text_ids=["r1"])
    result = normalize_final({
        "answer_markdown": "官方材料等级为[T1]，年份为[2026]；该主张有依据〔S1〕。",
        "citation_map": [{"anchor": "S1", "evidence_ids": ["r1"]}],
        "gaps": [],
    }, tools)
    assert "[T1]" in result["text"]
    assert "[2026]" in result["text"]
    assert "S1" not in result["text"]


def test_legacy_summary_explicit_evidence_remains_mapped_without_broad_fallback():
    tools = SimpleNamespace(retrieved_ids=["r1", "r2"], full_text_ids=["r1"])
    result = normalize_final({
        "summary": {"text": "最新线索仍待核验。", "evidence_ids": ["r1", "r2"]},
        "gaps": [],
    }, tools)
    assert result["citation_map"] == [{"anchor": "S1", "evidence_ids": ["r1"]}]
    assert result["evidence_ids"] == ["r1"]


def test_hybrid_retrieval_prefers_direct_topic_and_keeps_source_diversity():
    records = [
        record("direct", "美伊谈判条件更新", "伊朗与美国经调解方讨论谈判条件。", "2026-10-20T00:00:00Z", "official", "T1"),
        record("copy", "美伊谈判条件更新转载", "伊朗与美国经调解方讨论谈判条件。", "2026-10-20T01:00:00Z", "official", "T1"),
        record("other", "体育比赛结果", "文章尾部提及伊朗代表队。", "2026-10-23T00:00:00Z", "sport", "T3"),
        record("second", "卡塔尔继续斡旋美伊会谈", "卡塔尔表示调解仍在进行。", "2026-10-21T00:00:00Z", "wire", "T2"),
    ]
    result = hybrid_retrieve(records, "最新美伊外交谈判进展", limit=3)
    assert result[0]["id"] == "direct"
    assert "second" in [item["id"] for item in result]
    assert [item["source_id"] for item in result].count("official") == 1


def test_dynamic_evidence_budget_expands_for_cross_topic_research():
    assert evidence_budget("一句话解释", topic="usiran")["evidence"] == 24
    assert evidence_budget("分析美伊和俄乌对大类资产的影响", topic="all")["evidence"] == 96


def test_latest_retrieval_prefers_newer_near_equivalent_report():
    records = [
        record("older", "美伊谈判进展", "美国与伊朗继续讨论停火条件。", "2026-10-10T00:00:00Z", "wire-old"),
        record("newer", "美伊谈判最新进展", "美国与伊朗继续讨论停火条件。", "2026-10-23T00:00:00Z", "wire-new"),
    ]
    result = hybrid_retrieve(records, "最新美伊谈判进展", limit=2)
    assert result[0]["id"] == "newer"


def test_latest_retrieval_uses_recent_band_before_keyword_dense_old_background():
    records = [
        record(
            "old", "美伊外交谈判停火协议调解进展完整回顾",
            "美伊外交谈判停火协议调解进展历史背景。", "2026-09-01T00:00:00Z", "archive",
        ),
        record(
            "new", "卡塔尔称仍在接触美伊双方",
            "调解方就美伊局势继续接触双方。", "2026-10-23T00:00:00Z", "wire",
        ),
    ]
    result = hybrid_retrieve(records, "最新美伊外交谈判停火进展", limit=2)
    assert result[0]["id"] == "new"


def test_cross_source_exact_headline_is_not_counted_as_independent_evidence():
    records = [
        record("a", "美伊双方继续谈判", "媒体转述同一事件。", "2026-10-23T00:00:00Z", "wire-a"),
        record("b", "美伊双方继续谈判", "另一网站转载同一事件。", "2026-10-23T01:00:00Z", "wire-b"),
        record("c", "调解方公布下一轮安排", "卡塔尔公布会谈安排。", "2026-10-23T02:00:00Z", "wire-c"),
    ]
    result = hybrid_retrieve(records, "最新美伊谈判进展", limit=3)
    assert len([item for item in result if item["title"] == "美伊双方继续谈判"]) == 1
    assert "c" in [item["id"] for item in result]


def test_repeated_search_returns_only_new_evidence_and_metric_catalog_does_not_pollute_citations():
    records = [
        record("r1", "美伊谈判更新", "伊朗与美国继续接触。", "2026-10-22T00:00:00Z", "wire"),
        record("metric-source", "TACO 方法说明", "资产压力指标计算说明。", "2026-10-22T00:00:00Z", "market"),
    ]
    snapshot = {"id": "s1", "created_at": "2026-10-23T01:00:00Z", "records": records}
    tools = LocalGeoTools(
        snapshot=snapshot, topic="usiran", dimension="", start="", end="",
        budget=evidence_budget("最新美伊局势分析", topic="usiran"),
        filtered_records=lambda snapshot, *args: snapshot["records"],
        metrics=lambda *args: [{"series": "TACO_INDEX", "name": "TACO资产压力指数", "source_ids": ["metric-source"]}],
        analysis=lambda *args: {"assets": [], "scenarios": [], "data_warnings": [], "rule_version": "test"},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    first = tools.call("geo_evidence_search", {"q": "最新美伊谈判进展", "limit": 10})
    second = tools.call("geo_evidence_search", {"q": "美伊外交谈判", "limit": 10})
    tools.call("geo_metric_catalog", {"limit": 20})
    assert first["returned"] >= 1
    assert not ({item["id"] for item in first["records"]} & {item["id"] for item in second["records"]})
    assert "metric-source" not in [item["id"] for item in tools.citations()]


def test_parallel_internal_search_and_reads_share_atomic_budgets(monkeypatch):
    snapshot = {"id": "s1", "created_at": "2026-10-23T01:00:00Z", "records": []}
    budget = evidence_budget("最新美伊局势分析", topic="usiran")
    budget.update({"internal_messages": 1, "internal_full_text": 1})
    tools = LocalGeoTools(
        snapshot=snapshot, topic="usiran", dimension="", start="", end="", budget=budget,
        filtered_records=lambda snapshot, *args: [], metrics=lambda *args: [],
        analysis=lambda *args: {"assets": [], "scenarios": []},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    lock = threading.Lock()
    serial = 0

    def fake_search(**kwargs):
        nonlocal serial
        with lock:
            serial += 1
            current = serial
        return {"messages": [{"id": f"m{current}", "title": f"伊朗消息{current}"}], "total": 1}

    monkeypatch.setattr("backend.qa_agent.search_internal_messages", fake_search)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda query: tools.call("geo_internal_message_search", {"q": query, "limit": 1}),
            ["美伊消息一", "美伊消息二"],
        ))
    assert len(tools.internal_message_ids) == 1
    assert sum(len(item["messages"]) for item in results) == 1

    tools.internal_message_ids = ["m1", "m2"]
    monkeypatch.setattr("backend.qa_agent.get_internal_message", lambda mid: {"id": mid, "title": mid})

    def read(mid):
        try:
            return tools.call("geo_internal_message_get", {"message_id": mid})
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reads = list(pool.map(read, ["m1", "m2"]))
    assert len(tools.internal_full_text_ids) == 1
    assert sum(item is not None for item in reads) == 1


def test_follow_up_search_overfetches_past_already_seen_first_page():
    records = [
        record(f"r{index}", f"美伊外交谈判更新 {index}", f"双方继续接触，线索 {index}。", f"2026-10-{20 + index:02d}T00:00:00Z", f"wire-{index}")
        for index in range(1, 5)
    ]
    snapshot = {"id": "s1", "created_at": "2026-10-24T01:00:00Z", "records": records}
    budget = evidence_budget("最新美伊局势分析", topic="usiran")
    budget["search_page"] = 2
    tools = LocalGeoTools(
        snapshot=snapshot, topic="usiran", dimension="", start="", end="", budget=budget,
        filtered_records=lambda snapshot, *args: snapshot["records"], metrics=lambda *args: [],
        analysis=lambda *args: {"assets": [], "scenarios": []},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    first = tools.call("geo_evidence_search", {"q": "最新美伊外交谈判", "limit": 2})
    second = tools.call("geo_evidence_search", {"q": "最新美伊外交谈判", "limit": 2})
    assert first["returned"] == 2
    assert second["returned"] == 2
    assert not ({item["id"] for item in first["records"]} & {item["id"] for item in second["records"]})


def test_question_planner_parses_explicit_relative_window():
    plan = deterministic_plan(
        "分析近两个月美伊局势和资产影响",
        topic="usiran",
        dimension="",
        start="",
        end="",
        snapshot_as_of="2026-10-23T01:00:00Z",
    )
    assert plan["start"] == "2026-08-24"
    assert plan["end"] == "2026-10-23"
    assert plan["needs_metrics"] is True
    assert plan["budget"]["evidence"] == 64


def test_latest_situation_uses_a_one_week_window_and_topic_tracks():
    plan = deterministic_plan(
        "最新美伊冲突进展的情况分析", topic="usiran", dimension="",
        start="", end="", snapshot_as_of="2026-10-25T04:00:00Z",
    )
    assert plan["start"] == "2026-10-19"
    assert plan["end"] == "2026-10-25"
    assert any("胡塞" in query and "霍尔木兹" in query for query in plan["search_queries"])
    assert any("Trump" in query and "attack" in query for query in plan["search_queries"])
    assert plan["multi_agent"]["enabled"] is True
    assert [item["name"] for item in plan["time_windows"]] == ["latest_48h", "latest_7d", "background_30d"]
    assert plan["query_specs"]
    assert {item["lane"] for item in plan["query_specs"]} == {"core"}


def test_short_ukraine_latest_situation_is_complex_and_uses_bilingual_tracks():
    plan = deterministic_plan(
        "俄乌最新情况", topic="ukraine", dimension="",
        start="", end="", snapshot_as_of="2026-10-25T04:00:00Z",
    )
    assert plan["start"] == "2026-10-19"
    assert plan["end"] == "2026-10-25"
    assert plan["budget"]["evidence"] == 80
    assert plan["multi_agent"]["enabled"] is True
    assert set(plan["multi_agent"]["agents"]) == {"event", "market", "counterevidence"}
    assert any("ceasefire" in query and "Ukraine" in query for query in plan["search_queries"])
    assert any("frontline" in query and "drone" in query for query in plan["search_queries"])


def test_ukraine_full_text_priority_prefers_direct_war_records_over_russia_iran_background():
    records = [
        record("russia-iran", "Russia offers to mediate Iran talks", "俄罗斯提出斡旋伊朗问题。", "2026-10-25T03:00:00Z", "wire-a"),
        record("ukraine-talks", "Ukraine ceasefire talks planned", "乌克兰与俄罗斯讨论局部停火和会谈。", "2026-10-24T03:00:00Z", "wire-b"),
        record("ukraine-front", "Drone strike on Kyiv", "俄罗斯无人机袭击基辅，乌克兰防空拦截。", "2026-10-25T02:00:00Z", "wire-c"),
        record("ukraine-aid", "Patriot supply dispute", "乌克兰称爱国者拦截弹交付不足。", "2026-10-23T02:00:00Z", "wire-d"),
        record("ukraine-more", "Kherson humanitarian corridor", "赫尔松方向准备人道走廊。", "2026-10-24T02:00:00Z", "wire-e"),
    ]
    for item in records:
        item["topics"] = ["ukraine"]
        item["dimensions"] = {"ukraine": "frontline"}
    tools = LocalGeoTools(
        snapshot={"id": "s1", "records": records}, topic="ukraine", dimension="", start="", end="",
        budget=evidence_budget("俄乌最新情况", topic="ukraine"),
        filtered_records=lambda snapshot, *args: snapshot["records"], metrics=lambda *args: [],
        analysis=lambda *args: {"assets": [], "scenarios": []},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    tools.retrieved_ids = [item["id"] for item in records]
    chosen = tools.prioritized_evidence_ids("俄乌最新情况", 4)
    assert "russia-iran" not in chosen
    assert {"ukraine-talks", "ukraine-front", "ukraine-aid"}.issubset(chosen)


def test_ukraine_metric_catalog_prioritizes_topic_specific_transmission_series():
    items = [
        {"series": "gold", "name": "黄金", "current": 4000},
        {"series": "dgs10", "name": "10年期美债收益率", "current": 5},
        {"series": "ttf_gas", "name": "欧洲天然气TTF", "current": 93.85},
        {"series": "ru_usdrub_cbr", "name": "俄罗斯央行美元兑卢布官方汇率", "current": 80.54},
        {"series": "usduah", "name": "美元兑格里夫纳", "current": 45.03},
        {"series": "eu_ukraine_facility_disbursement", "name": "欧盟援乌实际拨款", "current": 2.9},
    ]
    tools = LocalGeoTools(
        snapshot={"id": "s1", "records": []}, topic="ukraine", dimension="", start="", end="",
        budget=evidence_budget("俄乌最新情况", topic="ukraine"),
        filtered_records=lambda snapshot, *args: [], metrics=lambda *args: items,
        analysis=lambda *args: {"assets": [], "scenarios": []},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    selected = tools.call("geo_metric_catalog", {"limit": 4})["metrics"]
    assert [item["series"] for item in selected] == [
        "ttf_gas", "ru_usdrub_cbr", "usduah", "eu_ukraine_facility_disbursement",
    ]


def test_recent_news_window_keeps_older_latest_monthly_metrics_visible():
    calls = []

    def scoped_metrics(snapshot, topic, dimension, start, end):
        calls.append((start, end))
        return [{
            "series": "eu_ukraine_facility_disbursement", "name": "欧盟援乌实际拨款",
            "current": {"date": "2026-10-02", "value": 2.9}, "source_ids": ["aid"],
        }]

    tools = LocalGeoTools(
        snapshot={"id": "s1", "records": []}, topic="ukraine", dimension="",
        start="2026-10-19", end="2026-10-25",
        budget=evidence_budget("俄乌最新情况", topic="ukraine"),
        filtered_records=lambda snapshot, *args: [], metrics=scoped_metrics,
        analysis=lambda *args: {"assets": [], "scenarios": []},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    result = tools.call("geo_metric_catalog", {"limit": 20})
    assert result["metrics"][0]["current"] == {"date": "2026-10-02", "value": 2.9}
    assert calls == [("", "2026-10-25")]


def test_event_full_text_priority_excludes_metric_projection_rows():
    records = [
        record("finance-brent", "布伦特原油更新", "市场指标投影。", "2026-10-25T02:00:00Z", "market"),
        record("event-latest", "Trump hints at Iran attack or deal", "特朗普谈及对伊行动或达成协议。", "2026-10-25T03:00:00Z", "wire"),
    ]
    snapshot = {"id": "s1", "created_at": "2026-10-25T04:00:00Z", "records": records}
    tools = LocalGeoTools(
        snapshot=snapshot, topic="usiran", dimension="", start="", end="",
        budget=evidence_budget("最新美伊局势分析", topic="usiran"),
        filtered_records=lambda snapshot, *args: snapshot["records"], metrics=lambda *args: [],
        analysis=lambda *args: {"assets": [], "scenarios": []},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    tools.retrieved_ids = ["finance-brent", "event-latest"]
    assert tools.prioritized_evidence_ids("Trump Iran attack deal", 1) == ["event-latest"]


def test_search_receipt_marks_budget_exhaustion_instead_of_false_no_hit():
    item = record("r1", "美伊进展", "双方继续接触。", "2026-10-25T00:00:00Z", "wire")
    budget = evidence_budget("最新美伊局势分析", topic="usiran")
    budget["evidence"] = 1
    tools = LocalGeoTools(
        snapshot={"id": "s1", "records": [item]}, topic="usiran", dimension="", start="", end="", budget=budget,
        filtered_records=lambda snapshot, *args: snapshot["records"], metrics=lambda *args: [],
        analysis=lambda *args: {"assets": [], "scenarios": []},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    tools.retrieved_ids = ["r1"]
    result = tools.call("geo_evidence_search", {"q": "美伊", "limit": 10})
    assert result["records"] == []
    assert result["budget_exhausted"] is True


def test_metric_catalog_prioritizes_asset_transmission_metrics():
    items = [
        {"series": "personnel", "name": "军事人员", "current": 10},
        {"series": "finance:brent", "name": "布伦特原油", "current": 116.72, "source_ids": ["m1"]},
        {"series": "TACO_INDEX", "name": "TACO资产压力指数", "current": 0.47, "source_ids": ["m2"]},
    ]
    tools = LocalGeoTools(
        snapshot={"id": "s1", "records": []}, topic="usiran", dimension="", start="", end="",
        budget=evidence_budget("最新美伊局势分析", topic="usiran"),
        filtered_records=lambda snapshot, *args: [], metrics=lambda *args: items,
        analysis=lambda *args: {"assets": [], "scenarios": []},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    result = tools.call("geo_metric_catalog", {"limit": 2})
    assert [item["series"] for item in result["metrics"]] == ["finance:brent", "TACO_INDEX"]
    assert result["metrics"][0]["source_ids"] == ["m1"]


def test_metric_catalog_exposes_stable_rounded_values_for_numeric_validation():
    items = [{
        "series": "brent", "name": "布伦特原油", "unit": "美元/桶",
        "current": {"date": "2026-10-23", "value": 116.72000122070312},
        "previous": {"date": "2026-10-22", "value": 113.16999816894531},
        "delta": 3.55000305175781, "pct": 3.13686123, "source_ids": ["m1"],
    }]
    tools = LocalGeoTools(
        snapshot={"id": "s1", "records": []}, topic="usiran", dimension="", start="", end="",
        budget=evidence_budget("最新美伊局势分析", topic="usiran"),
        filtered_records=lambda snapshot, *args: [], metrics=lambda *args: items,
        analysis=lambda *args: {"assets": [], "scenarios": []},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    metric = tools.call("geo_metric_catalog", {"limit": 20})["metrics"][0]
    assert metric["current"]["value"] == 116.72
    assert metric["previous"]["value"] == 113.17
    assert metric["delta"] == 3.55
    assert metric["pct"] == 3.14


def test_evidence_receipt_prefers_chinese_translation_and_keeps_original():
    item = record("r1", "Trump hints at Iran deal", "English body", "2026-10-25T00:00:00Z", "wire")
    item.update({"title_zh": "特朗普谈及伊朗协议", "text_zh": "中文正文"})
    summary = LocalGeoTools._record_summary(item)
    assert summary["title"] == "特朗普谈及伊朗协议"
    assert summary["original_title"] == "Trump hints at Iran deal"
    assert summary["excerpt"] == "中文正文"
    assert summary["original_excerpt"] == "English body"


def test_model_planner_cannot_erase_required_research_facets_or_metrics():
    async def complete(*args, **kwargs):
        return json.dumps({
            "focuses": ["事件主线"], "subquestions": ["发生了什么？"],
            "search_queries": ["美伊最新"], "needs_metrics": False,
            "needs_analysis": False, "needs_internal_messages": False,
        }, ensure_ascii=False), "fast-model"

    plan, model = asyncio.run(build_question_plan(
        "全面分析最新美伊局势、风险和资产影响", topic="usiran", dimension="",
        start="", end="", snapshot_as_of="2026-10-24T01:00:00Z", complete=complete,
    ))
    assert model == "fast-model"
    assert {"外交", "军事", "市场与资产", "反证与不确定性", "后续观察"}.issubset(plan["focuses"])
    assert plan["needs_metrics"] is True
    assert plan["needs_analysis"] is True
    assert plan["needs_internal_messages"] is True
    assert plan["budget"]["evidence"] == 80
    assert "美伊最新" in plan["search_queries"]


def test_agent_executes_tools_reads_full_text_and_returns_independent_citations():
    records = [
        record("r1", "美伊谈判更新", "双方立场仍有分歧，调解方继续接触。", "2026-10-22T00:00:00Z", "wire"),
        record("r2", "霍尔木兹通行更新", "通行状态仍需连续数据确认。", "2026-10-21T00:00:00Z", "maritime"),
    ]
    snapshot = {"id": "s1", "created_at": "2026-10-23T01:00:00Z", "records": records}

    def filtered(snapshot, topic, dimension, start, end):
        return snapshot["records"]

    def metrics(*args):
        return []

    def analysis(*args):
        return {"assets": [], "scenarios": [], "data_warnings": [], "rule_version": "test"}

    tools = LocalGeoTools(
        snapshot=snapshot,
        topic="usiran",
        dimension="",
        start="2026-10-09",
        end="2026-10-23",
        budget=evidence_budget("最新美伊局势分析", topic="usiran"),
        filtered_records=filtered,
        metrics=metrics,
        analysis=analysis,
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    plan = deterministic_plan(
        "最新美伊局势分析", topic="usiran", dimension="", start="", end="", snapshot_as_of=snapshot["created_at"]
    )
    plan["needs_internal_messages"] = False
    plan["multi_agent"]["enabled"] = False
    plan["multi_agent"]["agents"] = []
    plan["readiness_gate"] = False
    replies = iter([
        {"action": "tool", "tool": "geo_analysis_view", "arguments": {}, "purpose": "核对情景条件与反证"},
        {"action": "final", "answer_markdown": "## 结论\n\n美伊直接谈判仍有分歧，调解渠道尚在运行〔S1〕。", "citation_map": [{"anchor": "S1", "evidence_ids": ["r1"]}], "gaps": ["仍需官方文本"]},
    ])

    async def complete(messages, **kwargs):
        return json.dumps(next(replies), ensure_ascii=False), "reasoning-test"

    progress = []

    async def publish(item):
        progress.append(item)

    result = asyncio.run(run_agent(question="最新美伊局势分析", plan=plan, tools=tools, complete=complete, publish=publish))
    assert result["final"]["evidence_ids"] == ["r1"]
    assert result["final"]["citation_map"] == [{"anchor": "S1", "evidence_ids": ["r1"]}]
    assert "〔S1〕" not in result["final"]["text"]
    assert tools.full_text_ids
    assert any(call["tool"] == "geo_analysis_view" for call in result["tool_calls"])
    assert progress[-1]["stage"] == "synthesis"


def test_agent_can_search_and_read_huatai_internal_messages(monkeypatch):
    message = {
        "id": "hm1",
        "title": "原油与中东局势观察",
        "content_abstract": "内部消息提到原油与中东风险，仍需外部核验。",
        "message_at": "2026-10-22T08:00:00+08:00",
        "source": "华泰宏观消息源",
        "verification": "内部消息归档，内容须结合原始信源核验",
    }
    monkeypatch.setattr("backend.qa_agent.internal_message_stats", lambda: {"records": 1, "latest_message_at": message["message_at"]})
    monkeypatch.setattr(
        "backend.qa_agent.search_internal_messages",
        lambda **kwargs: {"messages": [dict(message)], "total": 1, "has_more": False, "query_scope": kwargs},
    )
    monkeypatch.setattr("backend.qa_agent.get_internal_message", lambda message_id: {**message, "ai_content": "完整内部消息正文"})
    snapshot = {"id": "s1", "created_at": "2026-10-23T01:00:00Z", "records": [record("r1", "美伊局势", "局势仍待核验。", "2026-10-22T00:00:00Z", "wire")]}
    tools = LocalGeoTools(
        snapshot=snapshot, topic="usiran", dimension="", start="2026-10-09", end="2026-10-23",
        budget=evidence_budget("最新美伊局势分析", topic="usiran"),
        filtered_records=lambda snapshot, *args: snapshot["records"],
        metrics=lambda *args: [],
        analysis=lambda *args: {"assets": [], "scenarios": [], "data_warnings": [], "rule_version": "test"},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    search = tools.call("geo_internal_message_search", {"q": "美伊", "limit": 5})
    detail = tools.call("geo_internal_message_get", {"message_id": "hm1"})
    assert search["messages"][0]["id"] == "hm1"
    assert detail["message"]["ai_content"] == "完整内部消息正文"
    assert tools.internal_sources()[0]["verification"].startswith("内部消息归档")
    stats = tools.call("geo_internal_message_stats", {})
    assert stats["records"] == 1
    assert tools.calls[-1]["result_count"] == 1


def test_huatai_search_filters_us_only_macro_noise_for_us_iran_question(monkeypatch):
    messages = [
        {
            "id": "relevant", "title": "伊朗与霍尔木兹风险更新",
            "content_abstract": "伊朗相关航运风险仍需核验。", "message_at": "2026-10-23T08:00:00+08:00",
            "group_name": "宏观群",
        },
        {
            "id": "noise", "title": "美国十年期国债观察",
            "content_abstract": "美国利率与就业数据。", "message_at": "2026-10-24T08:00:00+08:00",
            "group_name": "宏观群",
        },
    ]
    monkeypatch.setattr(
        "backend.qa_agent.search_internal_messages",
        lambda **kwargs: {"messages": [dict(item) for item in messages], "total": 2, "has_more": False, "query_scope": kwargs},
    )
    snapshot = {"id": "s1", "created_at": "2026-10-24T01:00:00Z", "records": []}
    tools = LocalGeoTools(
        snapshot=snapshot, topic="usiran", dimension="", start="", end="",
        budget=evidence_budget("最新美伊局势分析", topic="usiran"),
        filtered_records=lambda snapshot, *args: snapshot["records"], metrics=lambda *args: [],
        analysis=lambda *args: {"assets": [], "scenarios": []},
        event_metadata=lambda item: {"situation_signal": "unknown"},
    )
    result = tools.call("geo_internal_message_search", {"q": "最新美伊冲突进展", "limit": 10})
    assert [item["id"] for item in result["messages"]] == ["relevant"]


def test_citation_map_numeric_validation_removes_only_bad_sentence():
    records = {
        "r1": record("r1", "通行更新", "10月22日通行量为4艘次。", "2026-10-22T00:00:00Z", "wire")
    }
    text, removed = validate_citation_mapped_markdown(
        "已披露通行量为4艘次〔S1〕。\n未经支持的预测值为99%〔S1〕。\n仍需继续观察。",
        [{"anchor": "S1", "evidence_ids": ["r1"]}],
        records,
        [],
    )
    assert "4艘次" in text
    assert "99%" not in text
    assert "仍需继续观察" in text
    assert "99" in removed


def test_program_tool_receipt_numbers_survive_without_public_citation_anchor():
    text, removed = validate_citation_mapped_markdown(
        "| 指标 | 最新值 | 观测日期 |\n|---|---:|---|\n| 布伦特 | 109.59 | 2026-10-26 |",
        [], {}, [{
            "kind": "工具程序回执", "evidence_ids": [],
            "text": '{"name":"布伦特","current":109.59,"date":"2026-10-26"}',
        }],
    )
    assert "109.59" in text
    assert "2026-10-26" in text
    assert removed == set()


def test_numeric_validation_repairs_ordered_list_after_removing_bad_item():
    records = {
        "r1": record("r1", "局势更新", "外交接触仍在继续，市场变化不能单独证明因果。", "2026-10-22T00:00:00Z", "wire")
    }
    text, removed = validate_citation_mapped_markdown(
        "1. 外交接触仍在继续〔S1〕。\n2. 升级概率为0.07，样本为单日点值〔S1〕。\n4. 市场变化不能单独证明因果〔S1〕。",
        [{"anchor": "S1", "evidence_ids": ["r1"]}], records, [],
    )
    assert "0.07" not in text
    assert "样本为单日点值" not in text
    assert "1. 外交接触仍在继续" in text
    assert "2. 市场变化不能单独证明因果" in text
    assert "4. 市场" not in text
    assert removed == {"0.07"}


def test_numeric_validation_drops_an_invalid_markdown_table_row_as_a_unit():
    records = {"r1": record("r1", "市场更新", "WTI为103.27。", "2026-10-22T00:00:00Z", "wire")}
    text, removed = validate_citation_mapped_markdown(
        "| 指标 | 最新值 | 解读 |\n|---|---|---|\n| 布伦特 | 116.72 | 周涨7.3%暂无来源确认 |\n| WTI | 103.27 | 已有程序观测 |",
        [{"anchor": "S1", "evidence_ids": ["r1"]}], records, [],
    )
    assert "周涨" not in text
    assert "暂无来源确认" not in text
    assert "WTI | 103.27" in text
    assert "7.3" in removed
