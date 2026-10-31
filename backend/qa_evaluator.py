"""研究任务的证据级验收与可比较进展指纹。"""
from __future__ import annotations

from typing import Any

from .qa_claims import ClaimLedger, ClaimRecord, EvidenceRelation
from .qa_plan_schema import ProgressFingerprint, ResearchTask, TaskEvaluation
from .qa_scope import ResearchScope


_ACTUAL_ACTION_TERMS = ("实际", "已经发生", "已发生", "部署", "受损", "执行", "交付")
_FULL_TEXT_TERMS = ("正文", "原文", "全文", "原始来源", "原文位置")
_METRIC_TERMS = ("指标", "数值", "单位", "统计口径", "市场")
_COUNTER_TERMS = ("反证", "相反", "否认", "冲突", "失效")


def _task_claims(task: ResearchTask, ledger: ClaimLedger) -> list[ClaimRecord]:
    return [
        ledger.claims[claim_id]
        for claim_id in task.claim_ids
        if claim_id in ledger.claims and ledger.claims[claim_id].valid
    ]


def _relations(claims: list[ClaimRecord], ledger: ClaimLedger) -> list[EvidenceRelation]:
    claim_ids = {claim.claim_id for claim in claims}
    return [item for item in ledger.relations.values() if item.claim_id in claim_ids]


def _span_ref(relation: EvidenceRelation) -> str:
    if relation.span is None:
        return ""
    return (
        f"{relation.evidence_id}:{relation.span.start}-{relation.span.end}:"
        f"{relation.span.content_version}"
    )


def evaluate_task(
    task: ResearchTask,
    claims: ClaimLedger,
    scope: ResearchScope,
    *,
    model_result: dict[str, Any] | None = None,
) -> TaskEvaluation:
    """先做可复核的证据校验，再接受模型提出的冲突或补查线索。"""
    del scope  # 范围已在主张ID和工具调用阶段固定；保留参数以明确公共契约。
    model = model_result if isinstance(model_result, dict) else {}
    task_claims = _task_claims(task, claims)
    relations = _relations(task_claims, claims)
    supports = [item for item in relations if item.relation == "supports"]
    contradictions = [item for item in relations if item.relation == "contradicts"]
    full_text = [
        item for item in relations
        if item.span is not None and item.span.content_kind == "full_text"
    ]
    evidence_ids = list(dict.fromkeys(item.evidence_id for item in relations))
    span_refs = list(dict.fromkeys(value for item in relations if (value := _span_ref(item))))
    gaps: list[str] = []
    conflicts: list[str] = []
    suggested_action = ""

    if not task_claims:
        gaps.append("任务尚未形成绑定到本轮范围的主张")
    if task_claims and not supports:
        gaps.append("主张尚未绑定支持关系")
    if supports and not any(item in full_text for item in supports):
        gaps.append("支持关系尚未绑定可复核的正文位置与内容版本")
        suggested_action = "full_text_validation"

    criteria = task.acceptance_criteria or ["取得可追溯证据"]
    for criterion in criteria:
        text = str(criterion)
        if any(term in text for term in _FULL_TEXT_TERMS) and not full_text:
            gaps.append(f"验收条件“{text}”缺少正文位置与内容版本")
            suggested_action = suggested_action or "full_text_validation"
        if any(term in text for term in _ACTUAL_ACTION_TERMS):
            action_claims = [
                claim for claim in task_claims
                if claim.information_nature in {"verified_fact", "media_report"}
                and claim.verification in {"verified", "partial"}
            ]
            independently_supported = any(
                claim.information_nature == "verified_fact"
                or claims.independent_confirmation_count(claim.claim_id) >= 2
                for claim in action_claims
            )
            if not independently_supported:
                gaps.append(f"验收条件“{text}”目前只有表态、单方说法或单一来源")
                suggested_action = suggested_action or "actual_action_verification"
        if any(term in text for term in _METRIC_TERMS):
            metric_claims = [
                claim for claim in task_claims
                if claim.information_nature == "program_calculation" and claim.numeric_provenance
            ]
            if not metric_claims:
                gaps.append(f"验收条件“{text}”缺少带日期、单位和口径的程序计算")
                suggested_action = suggested_action or "metric_verification"
        if any(term in text for term in _COUNTER_TERMS) and not contradictions:
            searched = bool(model.get("counterevidence_searched"))
            search_scope = str(model.get("counterevidence_scope") or "").strip()
            if not searched or not search_scope:
                gaps.append(f"验收条件“{text}”尚未完成有范围记录的反证检索")
                suggested_action = suggested_action or "counterevidence"

    major_claim_ids = {
        str(item) for item in model.get("major_claim_ids") or [] if str(item)
    }
    for claim in task_claims:
        if (
            claim.claim_id in major_claim_ids
            and claim.information_nature in {"media_report", "single_party_claim"}
            and claims.independent_confirmation_count(claim.claim_id) < 2
        ):
            gaps.append("重大单一报道尚缺独立来源或原始官方材料核验")
            suggested_action = "source_verification"

    metric_conflict = str(model.get("metric_narrative_conflict") or "").strip()
    if metric_conflict:
        conflicts.append(metric_conflict)
        suggested_action = "metric_conflict_verification"
    if contradictions or any(claim.verification == "conflicted" for claim in task_claims):
        conflicts.extend(
            f"证据 {item.evidence_id} 与主张 {item.claim_id} 冲突"
            for item in contradictions
        )

    gaps = list(dict.fromkeys(gaps))
    conflicts = list(dict.fromkeys(conflicts))
    if conflicts:
        result = "conflicted"
        suggested_action = suggested_action or "source_verification"
    elif gaps:
        result = "incomplete"
    else:
        result = "satisfied"
    return TaskEvaluation(
        task_id=task.task_id,
        criterion_id="all",
        result=result,
        claim_ids=[claim.claim_id for claim in task_claims],
        evidence_ids=evidence_ids,
        span_refs=span_refs,
        gaps=gaps,
        conflicts=conflicts,
        suggested_action=suggested_action,
    )


def build_progress_fingerprint(
    claims: ClaimLedger,
    *,
    resolved_conflict_ids: list[str] | None = None,
    conclusion_boundaries: list[str] | None = None,
) -> ProgressFingerprint:
    """转载命中只按原始来源组计一次，正文按实际证据ID计。"""
    valid_claim_ids = sorted(
        claim.claim_id for claim in claims.claims.values() if claim.valid
    )
    origin_groups = sorted({
        relation.origin_group
        for relation in claims.relations.values()
        if relation.relation == "supports"
        and not relation.is_internal
        and relation.evidence_nature != "internal_message"
    })
    full_text_ids = sorted({
        relation.evidence_id
        for relation in claims.relations.values()
        if relation.span is not None and relation.span.content_kind == "full_text"
    })
    boundaries = list(conclusion_boundaries or [])
    boundaries.extend(
        f"{claim.claim_id}:{claim.verification}"
        for claim in claims.claims.values()
        if not claim.valid or claim.verification in {"conflicted", "invalidated"}
    )
    return ProgressFingerprint(
        independent_origin_groups=origin_groups,
        full_text_ids=full_text_ids,
        claim_ids=valid_claim_ids,
        resolved_conflict_ids=sorted(set(resolved_conflict_ids or [])),
        conclusion_boundaries=sorted(set(boundaries)),
    )
