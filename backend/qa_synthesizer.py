"""按用户问题组织研究主张，生成可做主张级校验的业务草稿。"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Awaitable, Callable

from .qa_claims import ClaimLedger, ClaimSpan
from .qa_plan_schema import TaskEvaluation, TaskProfile
from .qa_scope import ResearchScope


Complete = Callable[..., Awaitable[Any]]


@dataclass
class DraftAnswer:
    markdown: str
    claim_spans: dict[str, list[ClaimSpan]] = field(default_factory=dict)
    citation_map: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    stage_version: str = "draft/v1"
    business_lead: str = ""
    business_lead_claim_ids: list[str] = field(default_factory=list)
    status: str = "completed"
    model: str = ""

    @staticmethod
    def content_version(markdown: str) -> str:
        return "sha256:" + hashlib.sha256(str(markdown).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "claim_spans": {
                key: [
                    {
                        "start": span.start, "end": span.end,
                        "content_version": span.content_version, "quote": span.quote,
                        "content_kind": span.content_kind,
                    }
                    for span in values
                ]
                for key, values in self.claim_spans.items()
            },
            "citation_map": self.citation_map,
            "gaps": self.gaps,
            "stage_version": self.stage_version,
            "business_lead": self.business_lead,
            "business_lead_claim_ids": self.business_lead_claim_ids,
            "status": self.status,
            "model": self.model,
        }


def _topic_label(scope: ResearchScope) -> str:
    topics = set(scope.topics)
    if topics == {"usiran"}:
        return "美伊局势"
    if topics == {"ukraine"}:
        return "俄乌局势"
    return "用户所问的地缘局势"


def _valid_claims(claims: ClaimLedger) -> list[Any]:
    return [claim for claim in claims.claims.values() if claim.valid]


def _deterministic_lead(question: str, scope: ResearchScope, claims: ClaimLedger) -> tuple[str, list[str]]:
    values = _valid_claims(claims)
    preferred = [
        item for item in values
        if item.information_nature not in {"gap", "internal_message"}
        and item.verification != "invalidated"
    ]
    selected = preferred[:2]
    subject = _topic_label(scope)
    if not selected:
        return f"就“{question}”而言，当前可核验证据仍不足，暂不能形成可靠的方向判断。", []
    joined = "；".join(item.text.rstrip("。；") for item in selected)
    return f"{subject}当前最重要的判断是：{joined}。", [item.claim_id for item in selected]


def _structure(intent: str) -> str:
    if intent == "asset_implication":
        return "核心判断、关键驱动、传导机制、市场验证、反证与失效条件、后续观察"
    if intent in {"scenario_analysis", "prediction"}:
        return "核心判断、基准情景、尾部风险、触发条件、反证与失效条件、后续观察"
    if intent in {"fact_lookup", "source_verification"}:
        return "直接结论、证据与来源、口径与限制"
    if intent == "comparison_analysis":
        return "核心判断、共同变化、关键差异、原因与证据、反证、后续观察"
    return "核心判断、主要进展、判断依据、反证与不确定性、资产含义（如相关）、后续观察"


def _model_payload(
    question: str, profile: TaskProfile, scope: ResearchScope,
    claims: ClaimLedger, evaluations: list[TaskEvaluation],
) -> dict[str, Any]:
    return {
        "original_question": question,
        "intent": profile.intent,
        "required_structure": _structure(profile.intent),
        "scope": {
            "topics": list(scope.topics), "as_of": scope.data_as_of,
            "recent_start": scope.recent_start, "recent_end": scope.recent_end,
        },
        "claims": [claim.to_dict() for claim in _valid_claims(claims)],
        "relations": [
            relation.to_dict() for relation in claims.relations.values()
            if relation.claim_id in claims.claims
        ],
        "evaluations": [
            {
                "task_id": item.task_id, "result": item.result,
                "claim_ids": item.claim_ids, "gaps": item.gaps,
                "conflicts": item.conflicts,
            }
            for item in evaluations
        ],
    }


def _parse_model_result(value: Any) -> tuple[str, str, list[dict[str, Any]], str]:
    model = ""
    content = value
    if isinstance(value, tuple):
        content = value[0] if value else ""
        model = str(value[1] if len(value) > 1 else "")
    elif hasattr(value, "text"):
        content = value.text
        model = str(getattr(value, "model", ""))
    if isinstance(content, dict):
        data = content
    else:
        text = str(content or "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        try:
            parsed = json.loads(cleaned)
            data = parsed if isinstance(parsed, dict) else {"answer_markdown": text}
        except (ValueError, TypeError):
            data = {"answer_markdown": text}
    return (
        str(data.get("answer_markdown") or data.get("markdown") or "").strip(),
        str(data.get("business_lead") or "").strip(),
        [item for item in data.get("claim_map") or [] if isinstance(item, dict)],
        model,
    )


def _bad_business_lead(value: str) -> bool:
    text = re.sub(r"[#*`\s]", "", str(value or ""))
    if not text:
        return True
    internal = ("快照", "条记录", "个来源", "证据窗口", "T1级", "T2级", "T3级", "数据分布")
    return any(term in text for term in internal)


def _claim_spans(markdown: str, claims: ClaimLedger, proposed: list[dict[str, Any]]) -> dict[str, list[ClaimSpan]]:
    version = DraftAnswer.content_version(markdown)
    selected: dict[str, list[ClaimSpan]] = {}
    proposed_quotes: dict[str, list[str]] = {}
    for item in proposed:
        claim_id = str(item.get("claim_id") or "")
        quote = str(item.get("quote") or "").strip()
        if claim_id in claims.claims and quote:
            proposed_quotes.setdefault(claim_id, []).append(quote)
    for claim_id, claim in claims.claims.items():
        quotes = proposed_quotes.get(claim_id) or [claim.text]
        cursor = 0
        for quote in quotes:
            start = markdown.find(quote, cursor)
            if start < 0:
                continue
            selected.setdefault(claim_id, []).append(ClaimSpan(
                start, start + len(quote), version, quote, "full_text",
            ))
            cursor = start + len(quote)
    return selected


def _fallback_markdown(
    question: str, scope: ResearchScope, claims: ClaimLedger,
    evaluations: list[TaskEvaluation],
) -> tuple[str, str, list[str], list[str]]:
    lead, lead_ids = _deterministic_lead(question, scope, claims)
    values = _valid_claims(claims)
    facts = [f"- {claim.text}" for claim in values[:6]] or ["- 当前没有足够的可核验主张。"]
    conflicts = list(dict.fromkeys(
        conflict for evaluation in evaluations for conflict in evaluation.conflicts
    ))
    gaps = list(dict.fromkeys(gap for evaluation in evaluations for gap in evaluation.gaps))
    sections = [
        lead,
        "\n## 主要进展与依据",
        *facts,
        "\n## 反证与不确定性",
        *(f"- {item}" for item in conflicts),
        *(f"- {item}" for item in gaps),
        "- 报道、声明和市场同步变化均不能单独证明行动已经发生或存在因果关系。",
        "\n## 后续观察",
        "- 继续核对正式声明、实际行动、执行条件及能够推翻当前判断的证据。",
    ]
    return "\n".join(sections), lead, lead_ids, gaps


async def synthesize_answer(
    question: str,
    profile: TaskProfile,
    scope: ResearchScope,
    claims: ClaimLedger,
    evaluations: list[TaskEvaluation],
    *,
    complete: Complete,
    budget: Any,
) -> DraftAnswer:
    """模型只负责组织表达；事实、数字、冲突和引用边界来自结构化输入。"""
    del budget  # 费用与时限由调用适配器统一控制，避免综合器另建账本。
    payload = _model_payload(question, profile, scope, claims, evaluations)
    system = (
        "你是GeoAgent业务综合器。直接回答用户原始问题，不先介绍快照、记录数、来源数、"
        "证据等级分布或内部流程。正文使用完整中文Markdown，并按以下结构组织："
        f"{_structure(profile.intent)}。事实、声明、媒体转述、内部消息、程序计算和模型推论必须分开；"
        "数字只能逐字使用program_calculation主张；冲突并列保留，不得擅自裁决；内部消息只作待外部核验线索。"
        "返回JSON对象：answer_markdown、business_lead、claim_map。claim_map每项仅含claim_id和正文原句quote，"
        "引用映射由程序独立生成。business_lead用1—3句话给出全业务语境下的直接结论。"
    )
    try:
        raw = await complete(
            [{"role": "system", "content": system}, {
                "role": "user", "content": json.dumps(payload, ensure_ascii=False),
            }],
            model_role="synthesizer",
        )
        markdown, proposed_lead, proposed_map, model = _parse_model_result(raw)
        if not markdown or not re.search(r"[\u4e00-\u9fff]", markdown):
            raise ValueError("模型未生成完整中文正文")
        deterministic_lead, deterministic_ids = _deterministic_lead(question, scope, claims)
        lead = deterministic_lead if _bad_business_lead(proposed_lead) else proposed_lead
        lead_ids = deterministic_ids
        if proposed_lead and not _bad_business_lead(proposed_lead):
            mapped = [
                str(item.get("claim_id")) for item in proposed_map
                if str(item.get("claim_id") or "") in claims.claims
                and str(item.get("quote") or "") in proposed_lead
            ]
            lead_ids = list(dict.fromkeys(mapped)) or deterministic_ids
        if not markdown.lstrip().startswith(lead):
            markdown = f"{lead}\n\n{markdown}"
        spans = _claim_spans(markdown, claims, proposed_map)
        gaps = list(dict.fromkeys(gap for item in evaluations for gap in item.gaps))
        return DraftAnswer(
            markdown=markdown, claim_spans=spans, citation_map=[], gaps=gaps,
            stage_version="synthesis/v1", business_lead=lead,
            business_lead_claim_ids=lead_ids, status="completed", model=model,
        )
    except Exception as exc:
        markdown, lead, lead_ids, gaps = _fallback_markdown(question, scope, claims, evaluations)
        return DraftAnswer(
            markdown=markdown,
            claim_spans=_claim_spans(markdown, claims, []),
            citation_map=[], gaps=[*gaps, f"模型综合失败：{str(exc)[:160]}"],
            stage_version="synthesis/fallback-v1", business_lead=lead,
            business_lead_claim_ids=lead_ids, status="degraded",
        )
