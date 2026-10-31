"""问题任务画像与质量优先的执行模式路由。"""
from __future__ import annotations

import re
from typing import Any

from .qa_plan_schema import (
    BREADTHS, DEPTHS, FRESHNESS_LEVELS, INTENTS, RISK_LEVELS, ExecutionMode, TaskProfile,
)
from .qa_scope import ResearchScope


_MODE_RANK = {"fast": 0, "standard": 1, "research": 2}
_BREADTH_RANK = {"narrow": 0, "single_domain": 1, "multi_domain": 2, "cross_topic": 3}
_FRESHNESS_RANK = {"historical": 0, "current": 1, "latest": 2}
_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def _at_least(left: str, right: str) -> str:
    return left if _MODE_RANK[left] >= _MODE_RANK[right] else right


def _higher(left: str, right: str, ranks: dict[str, int]) -> str:
    return left if ranks[left] >= ranks[right] else right


def _deterministic_profile(question: str, scope: ResearchScope) -> TaskProfile:
    text = str(question or "").strip()
    forced = bool(re.search(r"强制(?:深度|深入)研究|深研模式|force\s*research", text, re.I))
    material_summary = bool(
        re.search(r"(?:以下|上述|所附|完整)材料", text)
        and re.search(r"总结|摘要|概括", text)
        and len(text) >= 180
    )
    narrow_lookup = bool(
        re.search(r"最新值|当前值|是多少|具体数值|原文|来源链接|哪条证据|发布日期", text)
        and not re.search(r"影响|原因|研判|情景|未来|风险|局势|综合", text)
    )
    asset = bool(re.search(r"资产|原油|油价|黄金|美债|收益率|信用利差|汇率|股指|配置", text))
    forecast = bool(re.search(
        r"会不会|会否|是否会|能否|会.{0,24}吗|未来|后续走势|情景|预测|可能性|概率|取决于什么",
        text,
    ))
    current = bool(
        re.search(r"最新|近期|当前|目前|现在|进展", text)
        and re.search(r"局势|情况|进展|动态|冲突|战争", text)
    )
    source_check = bool(re.search(r"核验|真假|来源是否|证据是否|谁说的|原始来源", text))

    if material_summary:
        intent, mode, confidence = "material_summary", "fast", 0.96
    elif narrow_lookup:
        intent, mode, confidence = "fact_lookup", "fast", 0.94
    elif asset:
        intent, mode, confidence = "asset_implication", "research", 0.91
    elif forecast:
        intent, mode, confidence = "scenario_analysis", "research", 0.9
    elif current:
        intent, mode, confidence = "current_situation", "research", 0.9
    elif source_check:
        intent, mode, confidence = "source_verification", "standard", 0.88
    elif re.search(r"对比|比较|差异", text):
        intent, mode, confidence = "comparison_analysis", "standard", 0.86
    elif re.search(r"为什么|原因|驱动", text):
        intent, mode, confidence = "causal_analysis", "standard", 0.85
    elif re.search(r"报告|简报", text):
        intent, mode, confidence = "report_generation", "research", 0.88
    else:
        intent, mode, confidence = "comprehensive_research", "standard", 0.72

    if forced:
        mode = "research"
    domains = sum(bool(re.search(pattern, text)) for pattern in (
        r"外交|谈判|制裁", r"军事|打击|战线|导弹|无人机",
        r"市场|资产|原油|黄金|美债", r"内政|民意|财政|援助",
    ))
    breadth = "cross_topic" if len(scope.topics) > 1 else "multi_domain" if domains >= 2 else "single_domain"
    if narrow_lookup or material_summary:
        breadth = "narrow"
    freshness = "latest" if re.search(r"最新|今天|今日|近期|当前|目前|现在|进展", text) else "current"
    risk = "high" if intent in {"scenario_analysis", "prediction", "asset_implication", "current_situation"} else "medium"
    if intent in {"fact_lookup", "material_summary"}:
        risk = "low"
    return TaskProfile(
        intent=intent, breadth=breadth,
        depth="deep" if mode == "research" else "shallow" if mode == "fast" else "normal",
        freshness=freshness, risk_level=risk,
        requires_counterevidence=intent in {"scenario_analysis", "prediction", "asset_implication", "current_situation"},
        requires_metrics=bool(asset or (narrow_lookup and re.search(r"值|数值|价格|收益率|指数", text))),
        requires_internal_sources=mode == "research" and "internal" in scope.source_permissions,
        suggested_mode=mode, confidence=confidence,
        reason="根据问题意图、时效性、研究广度和风险确定最低执行模式。",
        forced_research=forced,
    )


def _valid_model_result(value: dict[str, Any]) -> bool:
    checks = {
        "intent": INTENTS,
        "breadth": BREADTHS,
        "depth": DEPTHS,
        "freshness": FRESHNESS_LEVELS,
        "risk_level": RISK_LEVELS,
        "suggested_mode": {item.value for item in ExecutionMode},
    }
    for key, allowed in checks.items():
        if key in value and value[key] not in allowed:
            return False
    if "confidence" in value:
        try:
            confidence = float(value["confidence"])
        except (TypeError, ValueError):
            return False
        if not 0 <= confidence <= 1:
            return False
    return True


def classify_task(
    question: str, scope: ResearchScope, *, model_result: dict[str, Any] | None,
    force_research: bool = False,
) -> TaskProfile:
    """模型只能增强画像；确定性质量底线、强制深研和低置信度门槛不可降低。"""
    baseline = _deterministic_profile(question, scope)
    if force_research:
        baseline.suggested_mode = "research"
        baseline.depth = "deep"
        baseline.forced_research = True
    if not isinstance(model_result, dict) or not _valid_model_result(model_result):
        if model_result is not None:
            baseline.basis = "deterministic_fallback"
        return baseline

    proposed_mode = str(model_result.get("suggested_mode") or baseline.suggested_mode)
    mode = _at_least(baseline.suggested_mode, proposed_mode)
    confidence = float(model_result.get("confidence", baseline.confidence))
    if confidence < 0.65:
        mode = _at_least(mode, "standard")
    if baseline.forced_research:
        mode = "research"
    depth = str(model_result.get("depth") or baseline.depth)
    if mode == "research":
        depth = "deep"
    elif mode == "standard" and depth == "shallow":
        depth = "normal"
    may_replace_intent = _MODE_RANK[proposed_mode] >= _MODE_RANK[baseline.suggested_mode]
    intent = str(model_result.get("intent") or baseline.intent) if may_replace_intent else baseline.intent
    return TaskProfile(
        intent=intent,
        breadth=_higher(
            baseline.breadth, str(model_result.get("breadth") or baseline.breadth), _BREADTH_RANK,
        ),
        depth=depth,
        freshness=_higher(
            baseline.freshness, str(model_result.get("freshness") or baseline.freshness), _FRESHNESS_RANK,
        ),
        risk_level=_higher(
            baseline.risk_level, str(model_result.get("risk_level") or baseline.risk_level), _RISK_RANK,
        ),
        requires_counterevidence=bool(
            baseline.requires_counterevidence or model_result.get("requires_counterevidence", False)
        ),
        requires_metrics=bool(baseline.requires_metrics or model_result.get("requires_metrics", False)),
        requires_internal_sources=bool(
            "internal" in scope.source_permissions
            and (baseline.requires_internal_sources or model_result.get("requires_internal_sources", False))
        ),
        suggested_mode=mode, confidence=confidence,
        reason=str(model_result.get("reason") or baseline.reason),
        basis="model+deterministic", forced_research=baseline.forced_research,
    )
