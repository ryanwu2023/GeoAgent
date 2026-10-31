"""Validation-aware answer selection and deterministic fallback."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable

from .qa_claims import ClaimLedger, ClaimSpan
from .qa_synthesizer import DraftAnswer


@dataclass
class FinalAnswer:
    text: str
    annotated_text: str
    citation_map: list[dict[str, Any]] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    status: str = "completed"
    degraded_reason: str = ""
    business_lead: str = ""
    business_lead_claim_ids: list[str] = field(default_factory=list)
    stage_version: str = ""
    claim_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "annotated_text": self.annotated_text,
            "citation_map": self.citation_map,
            "evidence_ids": self.evidence_ids,
            "gaps": self.gaps,
            "status": self.status,
            "degraded_reason": self.degraded_reason,
            "business_lead": self.business_lead,
            "business_lead_claim_ids": self.business_lead_claim_ids,
            "stage_version": self.stage_version,
            "claim_ids": self.claim_ids,
        }


def finalize_validated(
    *, draft: dict[str, Any], normalize: Callable[[dict[str, Any]], dict[str, Any]],
) -> FinalAnswer:
    value = normalize(draft)
    return FinalAnswer(
        text=value["text"], annotated_text=value.get("annotated_text") or value["text"],
        citation_map=value.get("citation_map", []), evidence_ids=value.get("evidence_ids", []),
        gaps=value.get("gaps", []),
    )


def _valid_span(span: ClaimSpan, body: str, version: str) -> bool:
    try:
        return bool(span.extract(body, version))
    except ValueError:
        return False


def _claim_support(claim_id: str, ledger: ClaimLedger, memo: dict[str, bool]) -> bool:
    if claim_id in memo:
        return memo[claim_id]
    claim = ledger.claims.get(claim_id)
    if claim is None or not claim.valid or claim.verification == "invalidated":
        memo[claim_id] = False
        return False
    supports = [
        item for item in ledger.relations_for(claim_id)
        if item.relation == "supports" and item.span is not None
        and item.span.content_kind in {"full_text", "structured_value"}
    ]
    if claim.information_nature == "model_inference":
        premises = [
            item.premise_claim_id for item in ledger.dependencies
            if item.dependent_claim_id == claim_id
        ]
        # Mark temporarily to break accidental cycles in malformed legacy data.
        memo[claim_id] = False
        valid = bool(premises) and all(_claim_support(item, ledger, memo) for item in premises)
        memo[claim_id] = valid
        return valid
    valid = bool(supports)
    if claim.information_nature == "program_calculation":
        valid = valid and bool(claim.numeric_provenance) and all(
            item.calculation_complete for item in claim.numeric_provenance
        )
    memo[claim_id] = valid
    return valid


def _dependent_closure(ledger: ClaimLedger, rejected: set[str]) -> set[str]:
    value = set(rejected)
    changed = True
    while changed:
        changed = False
        for dependency in ledger.dependencies:
            if dependency.premise_claim_id in value and dependency.dependent_claim_id not in value:
                value.add(dependency.dependent_claim_id)
                changed = True
    return value


def _remove_claim_spans(body: str, spans: list[ClaimSpan]) -> str:
    value = body
    for span in sorted(spans, key=lambda item: (item.start, item.end), reverse=True):
        value = value[:span.start] + value[span.end:]
    value = re.sub(r"[。；]{2,}", "。", value)
    value = re.sub(r"(?m)^\s*[。；]\s*$", "", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _first_business_paragraph(text: str) -> str:
    for paragraph in re.split(r"\n\s*\n", str(text or "")):
        cleaned = re.sub(r"^#{1,6}\s*", "", paragraph.strip())
        if cleaned in {"核心判断", "结论", "结论摘要", "主要进展", "判断依据"}:
            continue
        if cleaned and re.search(r"[\u4e00-\u9fff]", cleaned):
            return cleaned
    return "当前证据不足以支持原草稿中的关键判断。"


def validate_and_repair(
    draft: DraftAnswer,
    claims: ClaimLedger,
    *,
    deadline: float,
) -> FinalAnswer:
    """按主张局部删除无支持内容；引用异常不能清空其余正文。"""
    del deadline  # 校验为确定性本地操作，即使进入收尾预留也必须完成。
    body = str(draft.markdown or "").strip()
    version = DraftAnswer.content_version(body)
    valid_spans: dict[str, list[ClaimSpan]] = {}
    invalid_span_claims: set[str] = set()
    for claim_id, spans in draft.claim_spans.items():
        kept = [span for span in spans if _valid_span(span, body, version)]
        if kept:
            valid_spans[claim_id] = kept
        elif spans:
            invalid_span_claims.add(claim_id)

    memo: dict[str, bool] = {}
    rejected = {
        claim_id for claim_id in valid_spans
        if not _claim_support(claim_id, claims, memo)
    }
    rejected.update(invalid_span_claims)
    rejected = _dependent_closure(claims, rejected)
    remove = [
        span for claim_id in rejected for span in valid_spans.get(claim_id, [])
    ]
    text = _remove_claim_spans(body, remove) if remove else body
    if not re.search(r"[\u4e00-\u9fff]", text):
        text = "当前证据不足以支持原草稿中的关键判断，暂不能形成可靠结论。"

    kept_claim_ids = [
        claim_id for claim_id in valid_spans
        if claim_id not in rejected and _claim_support(claim_id, claims, memo)
    ]
    mapping: list[dict[str, Any]] = []
    for claim_id in kept_claim_ids:
        evidence_ids = list(dict.fromkeys(
            relation.evidence_id
            for relation in claims.relations_for(claim_id)
            if relation.relation == "supports" and relation.span is not None
            and relation.span.content_kind in {"full_text", "structured_value"}
        ))
        if evidence_ids:
            mapping.append({
                "anchor": f"S{len(mapping) + 1}",
                "claim_ids": [claim_id],
                "evidence_ids": evidence_ids,
            })
    annotated = text
    for item in mapping:
        claim_id = item["claim_ids"][0]
        quote = next(
            (span.quote for span in valid_spans.get(claim_id, []) if span.quote in annotated),
            "",
        )
        if quote:
            annotated = annotated.replace(
                quote, f"{quote}〔{item['anchor']}〕", 1,
            )

    lead_ids = [
        claim_id for claim_id in draft.business_lead_claim_ids
        if claim_id in kept_claim_ids
    ]
    business_lead = draft.business_lead.strip() if (
        draft.business_lead.strip()
        and bool(draft.business_lead_claim_ids)
        and set(draft.business_lead_claim_ids).issubset(set(kept_claim_ids))
    ) else _first_business_paragraph(text)
    gaps = list(draft.gaps)
    if rejected:
        gaps.append("已删除缺少有效证据、正文位置或前提支持的局部主张。")
    return FinalAnswer(
        text=normalize_markdown_lists(text),
        annotated_text=normalize_markdown_lists(annotated),
        citation_map=mapping,
        evidence_ids=list(dict.fromkeys(
            evidence_id for item in mapping for evidence_id in item["evidence_ids"]
        )),
        gaps=list(dict.fromkeys(gaps))[:12],
        status="completed",
        business_lead=business_lead,
        business_lead_claim_ids=lead_ids,
        stage_version=draft.stage_version,
        claim_ids=kept_claim_ids,
    )


def _clean_draft(text: str) -> str:
    text = re.sub(r"\s*[〔\[]S?[A-Za-z0-9_-]+[〕\]]", "", str(text or ""))
    return text.strip()


def _is_reference_only(text: str) -> bool:
    return bool(re.fullmatch(r"[（(]?\s*(?:见上|同上|如上|见前文|答案见上)[。.!！]?\s*[）)]?", str(text or "").strip()))


_ORDERED_LIST = re.compile(r"^(\s*)\d+(\.(?=\s)|[、)])(\s*)(.*)$")
_EMPTY_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s*|\d+(?:\.\s*|[、)]\s*))$")
_NUMERIC_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])[-+]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?"
)


def _canonical_numeric_tokens(text: str) -> set[str]:
    """Return exact numeric values so one rejected digit cannot match a date."""
    from decimal import Decimal, InvalidOperation

    values: set[str] = set()
    for raw in _NUMERIC_TOKEN.findall(str(text or "")):
        token = raw.rstrip("%").replace(",", "")
        try:
            value = format(Decimal(token).normalize(), "f")
            if "." in value:
                value = value.rstrip("0").rstrip(".")
            values.add("0" if value in {"-0", "+0"} else value)
        except InvalidOperation:
            continue
    return values


def normalize_markdown_lists(text: str) -> str:
    """Remove empty list markers and renumber each contiguous ordered block.

    Numeric validation can remove a complete list item after the model has
    numbered the answer.  This presentation-only pass repairs the numbering;
    it never rewrites numbers inside the item body.
    """
    output: list[str] = []
    active_indent: str | None = None
    next_number = 1
    in_ordered_block = False
    for raw_line in str(text or "").splitlines():
        if _EMPTY_LIST_ITEM.fullmatch(raw_line):
            continue
        match = _ORDERED_LIST.match(raw_line)
        if match:
            indent, delimiter, spacing, body = match.groups()
            if not in_ordered_block or indent != active_indent:
                next_number = 1
            output.append(f"{indent}{next_number}{delimiter}{spacing}{body}")
            next_number += 1
            active_indent = indent
            in_ordered_block = True
            continue
        output.append(raw_line)
        # Blank lines and headings end a list. Indented continuation lines do
        # not, so wrapped Markdown list items retain a single numbering block.
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            in_ordered_block = False
            active_indent = None
        elif in_ordered_block and not raw_line.startswith((active_indent or "") + "  "):
            in_ordered_block = False
            active_indent = None
    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()


def _remove_rejected_numeric_sentences(text: str, failure: str) -> str:
    """Remove only sentences containing numbers named by validation errors.

    Citation transport failures should not erase an otherwise useful Chinese
    answer.  When validation identifies concrete unsupported numbers, remove
    the affected sentence/line and keep the surrounding sourced analysis.
    """
    if not text or not re.search(r"数值|数字|numeric", failure or "", re.I):
        return text
    # Values are listed after the final Chinese/ASCII colon.  Restrict parsing
    # to that suffix so status text or error codes do not become rejected
    # factual values.
    failure_values = re.split(r"[：:]", str(failure or ""))[-1]
    rejected = _canonical_numeric_tokens(failure_values)
    if not rejected:
        return text
    output = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and (_canonical_numeric_tokens(stripped) & rejected):
            continue
        if (_ORDERED_LIST.match(line) or re.match(r"^\s*[-*+]\s+", line)) and (_canonical_numeric_tokens(line) & rejected):
            # Drop the complete item. Keeping the tail of a rejected numbered
            # claim produces an orphaned phrase with misleading semantics.
            continue
        parts = re.split(r"(?<=[。！？；])", line)
        kept = [part for part in parts if not (_canonical_numeric_tokens(part) & rejected)]
        if "".join(kept).strip() or not line.strip():
            output.append("".join(kept).rstrip())
    return normalize_markdown_lists("\n".join(output))


def _compact_text(value: Any, maximum: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= maximum else text[:maximum].rstrip() + "…"


def _metric_line(item: dict[str, Any]) -> str:
    """Render program values without asking the model to restate numbers."""
    name = str(item.get("name") or item.get("series") or "指标")
    current = item.get("current")
    date = item.get("date") or item.get("observed_at") or ""
    if isinstance(current, dict):
        value = current.get("value")
        date = current.get("date") or date
    else:
        value = current
    unit = str(item.get("unit") or "").strip()
    parts: list[str] = []
    if date:
        parts.append(f"观测日期 {str(date)[:10]}")
    previous = item.get("previous")
    if isinstance(previous, dict):
        previous = previous.get("value")
    if previous is not None:
        parts.append(f"前值 {previous}{(' ' + unit) if unit else ''}")
    if item.get("delta") is not None:
        parts.append(f"变化 {item['delta']}{(' ' + str(item.get('delta_unit') or unit)) if (item.get('delta_unit') or unit) else ''}")
    if item.get("pct") is not None:
        parts.append(f"相对前期 {item['pct']}%")
    suffix = f"（{'；'.join(parts)}）" if parts else ""
    return f"- {name}：{value}{(' ' + unit) if unit else ''}{suffix}"


def _ledger_uncertainties(research_memory: dict[str, Any]) -> list[str]:
    ledger = research_memory.get("ledger")
    entries = ledger.get("entries", []) if isinstance(ledger, dict) else []
    if isinstance(entries, dict):
        entries = list(entries.values())
    result: list[str] = []
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict) or item.get("status") not in {"conflicted", "gap"}:
            continue
        claim = _compact_text(item.get("claim"), 220)
        if claim and claim not in result:
            result.append(claim)
    return result[:5]


def deterministic_fallback(
    *, last_valid_draft: str, research_memory: dict[str, Any], evidence: list[dict[str, Any]],
    metrics: list[dict[str, Any]] | None, internal_messages: list[dict[str, Any]],
    scope: Any, failure: str,
) -> FinalAnswer:
    draft = _clean_draft(last_valid_draft)
    # Keep a readable answer if citation metadata fails.  If numeric
    # validation names unsupported values, remove just those sentences; the
    # deterministic evidence summary below still guarantees a non-empty answer.
    draft = _remove_rejected_numeric_sentences(draft, str(failure or ""))
    used_draft = bool(draft and not _is_reference_only(draft) and re.search(r"[\u4e00-\u9fff]", draft))
    fallback_mapping: list[dict[str, Any]] = []
    if used_draft:
        text = draft
    else:
        facts = []
        cited_facts: list[tuple[str, str]] = []
        themes = []
        ordered_evidence = sorted(
            (item for item in evidence if isinstance(item, dict)),
            key=lambda item: str(item.get("published_at") or item.get("date") or ""), reverse=True,
        )
        fact_details: list[str] = []
        for item in ordered_evidence[:8]:
            title = str(item.get("title_zh") or item.get("title") or "").strip()
            source = str(item.get("source_name") or item.get("source") or "公开来源").strip()
            published = str(item.get("published_at") or "时间未标注")
            if title:
                fact = f"- {title}（{source}，{published}）"
                facts.append(fact)
                themes.append(title.rstrip("。！？"))
                detail = _compact_text(
                    item.get("summary_zh") or item.get("text_zh") or item.get("translated_text")
                    or item.get("summary") or item.get("text"),
                )
                if detail and detail.lower() not in title.lower():
                    tier = str(item.get("tier") or "").upper()
                    boundary = "；来源层级 " + tier if tier else ""
                    fact_details.append(f"  - 已读要点：{detail}{boundary}")
                else:
                    fact_details.append("")
                if item.get("id"):
                    anchor = f"S{len(fallback_mapping) + 1}"
                    fallback_mapping.append({
                        "anchor": anchor,
                        "evidence_ids": [str(item["id"])],
                    })
                    cited_facts.append((fact, anchor))
        metric_lines = []
        for item in (metrics or [])[:8]:
            name = item.get("name") or item.get("series")
            current = item.get("current")
            if name and current is not None:
                metric_lines.append(_metric_line(item))
        topic = str(getattr(scope, "topic", "") or "")
        subject = "美伊局势" if topic == "usiran" else "俄乌局势" if topic == "ukraine" else "当前地缘局势"
        if themes:
            theme_text = "；".join(themes[:3])
            conclusion = (
                f"截至当前数据范围，{subject}最新可追溯材料主要集中在：{theme_text}。"
                "模型服务本轮未完成综合，因此以下结论只按已取得的来源、正文和程序指标收敛："
                "可以确认这些事项已被报道或相关主体已作表态，但不能把计划、单方声明或媒体转述直接写成已经发生的行动。"
            )
        else:
            conclusion = f"当前范围尚未取得足以形成{subject}方向判断的公开证据，暂不能可靠判断局势变化。"
        mainline: list[str] = []
        for fact, detail in zip(facts, fact_details):
            mainline.append(fact)
            if detail:
                mainline.append(detail)
        uncertainties = _ledger_uncertainties(research_memory)
        sections = [
            "## 结论摘要",
            conclusion,
            "\n## 最新进展与依据（事件主线）",
            *(mainline or ["- 当前范围没有取得足以形成明确事件结论的公开证据。"]),
            "\n## 判断与不确定性（含反证）",
            "- 上述内容是已检索报道线索的归纳，不等于事件已经得到独立核实；官方表态只能证明其作出表态，媒体转述、行动计划和实际行动必须分别核对。",
            "- 报道数量不是情景概率，市场价格同步变化也不能单独证明由冲突造成。",
            *(f"- {item}" for item in uncertainties),
        ]
        if metric_lines:
            sections += ["\n## 量化观察", *metric_lines]
        if internal_messages:
            sections += ["\n## 华泰内部消息补充", "内部消息仅用于发现线索，仍需公开或官方来源核验。"]
            for item in internal_messages[:3]:
                sections.append(f"- {item.get('title') or item.get('content_abstract') or item.get('id')}（内部消息 ID：{item.get('id')}）")
        gaps = research_memory.get("gaps") or []
        watch = (
            [
                "核对美伊双方、以色列及调解方是否出现带时间和执行条件的正式声明。",
                "区分军事筹划、撤员或警告与已经发生的行动，并寻找第二个独立来源确认。",
                "跟踪霍尔木兹绝对通行量、原油、通胀预期与避险资产是否形成持续而非单日的共同变化。",
            ]
            if topic == "usiran" else
            [
                "核对俄乌双方战线与外交声明是否出现可执行的时间、地点和条件。",
                "区分援助承诺、已经拨付与实际交付，并跟踪防空和远程打击能力变化。",
                "观察欧洲能源、卢布、俄罗斯财政与乌克兰外部收支是否形成持续变化。",
            ]
            if topic == "ukraine" else
            ["继续核对最新官方声明、行动层面证据、关键量化指标及与当前判断相反的材料。"]
        )
        sections += [
            "\n## 数据缺口",
            *(f"- {item}" for item in gaps[:5]),
            "- 模型综合或引用校验未完整完成，不能把缺失信息补写为事实。",
            "\n## 后续观察",
            *(f"{index}. {item}" for index, item in enumerate(watch, 1)),
        ]
        text = "\n".join(sections)
    text = normalize_markdown_lists(text)
    annotated_text = text
    if not used_draft:
        for fact, anchor in cited_facts:
            annotated_text = annotated_text.replace(fact, f"{fact}〔{anchor}〕", 1)
    reason = failure or "模型综合未完整完成"
    gaps = list(dict.fromkeys([*(research_memory.get("gaps") or []), reason]))[:8]
    if used_draft and evidence:
        gaps = list(dict.fromkeys([
            *gaps,
            "最终草稿缺少可靠的逐主张引用映射；已保留正文并省略不可靠引用。",
        ]))[:8]
    mapping = fallback_mapping if not used_draft else []
    evidence_ids = [rid for item in mapping for rid in item["evidence_ids"]]
    return FinalAnswer(
        text=text, annotated_text=annotated_text, citation_map=mapping, evidence_ids=evidence_ids,
        gaps=gaps, status="degraded", degraded_reason=reason,
    )
