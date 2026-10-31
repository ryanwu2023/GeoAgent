from datetime import datetime, timezone

from backend.qa_harness_state import HarnessScope
from backend.qa_router import classify_task
from backend.qa_scope import ResearchScope


def scope(question: str = "最新美伊局势") -> ResearchScope:
    return ResearchScope.from_question(
        question, view_scope=HarnessScope("s1", "usiran"),
        snapshot_created_at="2026-10-31T01:00:00Z",
        now=datetime(2026, 10, 31, 2, 0, tzinfo=timezone.utc),
    )


def test_scenario_question_uses_research_mode():
    question = "伊朗会封锁霍尔木兹海峡吗？"
    profile = classify_task(question, scope(question), model_result=None)
    assert profile.intent in {"scenario_analysis", "prediction"}
    assert profile.suggested_mode == "research"
    assert profile.requires_counterevidence is True


def test_complete_material_simple_summary_can_use_fast_mode():
    material = "以下是完整材料：" + ("双方于今日举行会谈，尚未公布联合声明。" * 20)
    profile = classify_task(f"请只总结以下完整材料，不要补充外部信息。{material}", scope(), model_result=None)
    assert profile.intent == "material_summary"
    assert profile.suggested_mode == "fast"


def test_low_model_confidence_is_at_least_standard():
    profile = classify_task(
        "这条消息是什么意思？", scope(),
        model_result={
            "intent": "fact_lookup", "breadth": "narrow", "depth": "shallow",
            "freshness": "current", "risk_level": "low", "suggested_mode": "fast",
            "confidence": 0.3, "reason": "不确定",
        },
    )
    assert profile.suggested_mode == "standard"


def test_force_research_and_deterministic_floor_cannot_be_downgraded_by_model():
    forced = classify_task(
        "强制深度研究：核对这条消息", scope(),
        model_result={"suggested_mode": "fast", "confidence": 0.99},
    )
    assert forced.suggested_mode == "research"

    question = "最新美伊局势及其大类资产影响"
    not_downgraded = classify_task(
        question, scope(question),
        model_result={
            "intent": "fact_lookup", "breadth": "narrow", "depth": "shallow",
            "freshness": "latest", "risk_level": "low", "suggested_mode": "fast",
            "confidence": 0.99,
        },
    )
    assert not_downgraded.suggested_mode == "research"
    assert not_downgraded.requires_metrics is True

    explicit = classify_task(
        "核对这一条来源", scope(), model_result=None, force_research=True,
    )
    assert explicit.suggested_mode == "research"
    assert explicit.forced_research is True


def test_invalid_model_payload_falls_back_to_deterministic_profile():
    question = "最新俄乌局势"
    profile = classify_task(question, scope(question), model_result={"intent": "unknown", "confidence": "high"})
    assert profile.intent == "current_situation"
    assert profile.suggested_mode == "research"
    assert profile.basis == "deterministic_fallback"
