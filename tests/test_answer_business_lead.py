import asyncio

import pytest

from backend.answer_business_lead import (
    build_business_lead_context,
    fallback_business_lead,
    generate_business_lead,
)


def record(rid, topic, title, text="相关事实与后续触发条件仍待核验。"):
    return {
        "id": rid,
        "topics": [topic],
        "dimensions": {topic: "diplomacy"},
        "dimension_candidates": {topic: ["diplomacy"]},
        "title": title,
        "text": text,
        "source_name": "示例来源",
        "published_at": "2026-10-26T10:00:00Z",
        "tier": "T2",
        "verification": "待核验",
        "supplemental": False,
    }


@pytest.fixture
def snapshot():
    return {
        "id": "snapshot",
        "created_at": "2026-10-27T04:00:00Z",
        "records": [
            record("u1", "usiran", "美伊谈判与军事压力并行", "美伊谈判仍在继续，军事压力上升，后续取决于官方行动。"),
            record("k1", "ukraine", "俄乌停火接触出现新线索", "俄乌接触增加，但远程打击仍在持续。"),
        ],
        "observations": [],
    }


def answer(query, topic="all", citation_ids=()):
    return {
        "query": query,
        "topic": topic,
        "filters": {"dimension": "", "start": "", "end": ""},
        "summary": {"text": "## 一、结论摘要\n\n局势处于高压僵持，后续取决于官方行动与谈判落实。", "evidence_ids": list(citation_ids)},
        "citations": [],
        "gaps": ["缺少共同确认文件。"],
    }


@pytest.mark.parametrize(
    "query,topic,scope,ids",
    [
        ("美伊冲突最新进展", "usiran", "usiran", {"u1"}),
        ("俄乌冲突最新情况", "ukraine", "ukraine", {"k1"}),
        ("比较美伊和俄乌的最新走向", "usiran", "cross_topic", {"u1", "k1"}),
        ("霍尔木兹通行变化如何影响油价", "usiran", "specific_issue", {"u1"}),
    ],
)
def test_context_scope_follows_original_question(snapshot, query, topic, scope, ids):
    context = build_business_lead_context(answer(query, topic), snapshot)
    assert context["scope"] == scope
    assert {item["id"] for item in context["evidence"]} == ids


def test_generate_valid_business_lead(snapshot):
    lead = (
        "美伊局势正处于军事压力加速、外交窗口尚未关闭的高压僵持阶段。近期最关键变化是谈判信号与军事戒备同步增强，"
        "这意味着任何单方表态都不足以确认局势已经转向。后续走势主要取决于双方是否出现可核验的直接或间接接触、"
        "实际部署是否继续扩大，以及关键航道运行能否稳定。若紧张持续，能源风险溢价和通胀预期可能承压上行；若接触落实并伴随行动降温，风险资产压力才可能缓解。"
    )

    async def complete(messages, **kwargs):
        return (
            '{"business_lead":"' + lead + '","evidence_ids":["u1"],"metric_series":[],"scope":"usiran"}',
            "test-model",
        )

    result = asyncio.run(generate_business_lead(answer("美伊冲突最新进展", "usiran"), snapshot, complete))
    assert result.mode == "model"
    assert result.scope == "usiran"
    assert result.evidence_ids == ("u1",)
    assert result.text == lead


def test_generate_trims_only_complete_sentence_when_model_slightly_overshoots(snapshot):
    first = (
        "俄乌局势处于局部停火试探与远程打击持续并行的战术性停顿阶段，近期接触增加但仍不足以证明全面停火已经形成。"
        "后续主要取决于局部安排能否持续执行、直接谈判是否形成正式文件，以及外部援助与远程打击是否继续升级；"
        "这些条件未同时改善前，缓和只能视为有限窗口，不能外推为冲突结束。"
    )
    second = "资产层面仍应继续观察能源、汇率与欧洲利率是否出现同向变化，单一市场价格不能证明冲突变量已经成为主要驱动。" * 2

    async def complete(messages, **kwargs):
        return (
            '{"business_lead":"' + first + second + '","evidence_ids":["k1"],"metric_series":[],"scope":"ukraine"}',
            "test-model",
        )

    result = asyncio.run(generate_business_lead(answer("俄乌冲突最新情况", "ukraine"), snapshot, complete))
    assert result.mode == "model"
    assert 150 <= len(result.text) <= 300
    assert result.text.endswith(("。", "；", "！", "？"))


@pytest.mark.parametrize("mode", ["timeout", "invalid_json", "unknown_id", "unsupported_number", "too_short", "metadata"])
def test_invalid_model_output_uses_safe_fallback(snapshot, mode):
    async def complete(messages, **kwargs):
        if mode == "timeout":
            raise asyncio.TimeoutError("secret https://model.example/key")
        if mode == "invalid_json":
            return "不是JSON", "test"
        text = (
            "美伊局势处于高压僵持并保留外交窗口。近期谈判信号与军事戒备同时增加，但单方声明不足以证明行动已经发生。"
            "后续主要取决于官方接触能否落实、军事部署是否扩大以及关键航道运行是否稳定。若紧张延续，能源风险溢价可能上升；"
            "若谈判伴随实际降温，市场压力才可能缓解。研究判断应继续区分公开表态、媒体转述与已经核验的执行结果。"
        )
        evidence = '["missing"]' if mode == "unknown_id" else '["u1"]'
        if mode == "unsupported_number":
            text += "预计油价上涨88%。"
        if mode == "too_short":
            text = "局势仍在僵持，后续取决于谈判。"
        if mode == "metadata":
            text = "截至当前快照，" + text
        return f'{{"business_lead":"{text}","evidence_ids":{evidence},"metric_series":[],"scope":"usiran"}}', "test"

    result = asyncio.run(generate_business_lead(answer("美伊冲突最新进展", "usiran"), snapshot, complete))
    assert result.mode == "fallback"
    assert "https://" not in result.warning
    assert "高压僵持" in result.text


def test_fallback_filters_metadata_opening():
    item = answer("美伊冲突最新进展", "usiran")
    item["summary"]["text"] = (
        "截至2026年10月27日快照，本轮检索获得大量材料。\n\n"
        "美伊局势处于高压僵持，近期军事压力上升，但外交窗口仍未关闭，后续取决于双方实际行动与谈判落实。"
    )
    result = fallback_business_lead(item)
    assert not result.startswith("截至")
    assert result.startswith("美伊局势")
