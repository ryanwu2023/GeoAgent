"""为问答 HTML 导出生成可校验的业务核心判断。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import llm
from .research import filtered_records, metrics, numeric_tokens, retrieve


_SPECIFIC_PATTERNS = re.compile(
    r"霍尔木兹|曼德海峡|油价|原油|天然气|黄金|美债|收益率|通胀|信用利差|"
    r"援乌|军援|制裁|停火|谈判|会晤|航运|粮食|卢布|无人机|核设施",
    re.I,
)
_USIRAN_PATTERNS = re.compile(r"美伊|美国.{0,8}伊朗|伊朗.{0,8}美国|伊朗|霍尔木兹|胡塞|Hormuz|Houthi|\bIran(?:ian)?\b", re.I)
_UKRAINE_PATTERNS = re.compile(r"俄乌|俄罗斯.{0,8}乌克兰|乌克兰.{0,8}俄罗斯|乌克兰|援乌|Ukraine|Russia", re.I)
_META_OPENING = re.compile(r"^(?:截至(?:当前|今日|现在|20\d{2})|本轮(?:检索|取数)|证据窗口|T[123](?:/T[123])?|当前快照)", re.I)
_BUSINESS_TERMS = re.compile(r"升级|缓和|僵持|停顿|持续|转向|分化|窗口|取决于|若|如果|关键|风险|压力|影响|意味着")
_ASSET_TERMS = re.compile(r"资产|油价|原油|黄金|美债|收益率|通胀|信用|权益|股市|汇率|美元|天然气|粮食", re.I)


@dataclass(frozen=True)
class BusinessLeadResult:
    text: str
    scope: str
    mode: str
    evidence_ids: tuple[str, ...] = ()
    metric_series: tuple[str, ...] = ()
    warning: str = ""


def _scope_for(answer: dict) -> str:
    question = str(answer.get("query") or "")
    has_usiran = bool(_USIRAN_PATTERNS.search(question))
    has_ukraine = bool(_UKRAINE_PATTERNS.search(question))
    if has_usiran and has_ukraine:
        return "cross_topic"
    if _SPECIFIC_PATTERNS.search(question):
        return "specific_issue"
    if has_usiran:
        return "usiran"
    if has_ukraine:
        return "ukraine"
    topic = str(answer.get("topic") or "all")
    return topic if topic in {"usiran", "ukraine"} else "cross_topic"


def _topics_for(answer: dict, scope: str) -> list[str]:
    question = str(answer.get("query") or "")
    topics = []
    if _USIRAN_PATTERNS.search(question):
        topics.append("usiran")
    if _UKRAINE_PATTERNS.search(question):
        topics.append("ukraine")
    if not topics and answer.get("topic") in {"usiran", "ukraine"}:
        topics.append(answer["topic"])
    if scope == "cross_topic" and not topics:
        topics = ["usiran", "ukraine"]
    return topics or ["usiran", "ukraine"]


def _compact_record(record: dict) -> dict:
    return {
        "id": str(record.get("id") or ""),
        "topic": list(record.get("topics") or []),
        "title": str(record.get("title") or "")[:240],
        "text": str(record.get("text") or "")[:900],
        "source_name": str(record.get("source_name") or "来源未标注")[:100],
        "published_at": str(record.get("published_at") or ""),
        "tier": str(record.get("tier") or "未分级"),
        "verification": str(record.get("verification") or "待核验"),
    }


def _compact_metric(item: dict) -> dict:
    return {
        "series": str(item.get("series") or ""),
        "name": str(item.get("name") or ""),
        "unit": str(item.get("unit") or ""),
        "scope": str(item.get("scope") or ""),
        "current": item.get("current"),
        "previous": item.get("previous"),
        "delta": item.get("delta"),
        "delta_unit": item.get("delta_unit"),
        "pct": item.get("pct"),
        "boundary": str(item.get("boundary") or "")[:240],
    }


def _metric_relevant(question: str, item: dict) -> bool:
    haystack = f"{item.get('series', '')} {item.get('name', '')}".lower()
    groups = {
        "energy": (r"油|原油|能源|霍尔木兹|航运|天然气", ("brent", "wti", "hormuz", "gas", "xle")),
        "gold": (r"黄金|避险", ("gold",)),
        "rates": (r"美债|国债|收益率|通胀|利率", ("dgs", "treasury", "breakeven", "yield", "10y")),
        "credit": (r"信用|利差|风险偏好", ("oas", "vix", "taco")),
        "fx": (r"汇率|美元|卢布|欧元", ("dxy", "usd", "eur")),
        "food": (r"粮食|小麦|玉米", ("wheat", "corn", "cereal")),
    }
    matched = [needles for pattern, needles in groups.values() if re.search(pattern, question, re.I)]
    if not matched:
        return bool(item.get("current"))
    return any(any(needle in haystack for needle in needles) for needles in matched)


def build_business_lead_context(answer: dict, snapshot: dict) -> dict:
    """构建同一快照内、有界且按用户问题定向的模型材料。"""
    scope = _scope_for(answer)
    topics = _topics_for(answer, scope)
    question = str(answer.get("query") or "").strip()
    filters = answer.get("filters") if isinstance(answer.get("filters"), dict) else {}
    start, end = str(filters.get("start") or ""), str(filters.get("end") or "")

    record_by_id = {str(item.get("id")): item for item in snapshot.get("records", []) if item.get("id")}
    selected = []
    seen = set()
    for item in answer.get("citations", []):
        rid = str(item.get("id") or "") if isinstance(item, dict) else ""
        record = record_by_id.get(rid, item if isinstance(item, dict) else None)
        if not record or rid in seen or not set(record.get("topics") or []) & set(topics):
            continue
        selected.append(record)
        seen.add(rid)

    per_topic_limit = 6 if len(topics) > 1 else 10
    for topic in topics:
        records = filtered_records(snapshot, topic, "", start, end)
        for record in retrieve(records, question, limit=per_topic_limit):
            rid = str(record.get("id") or "")
            if rid and rid not in seen:
                selected.append(record)
                seen.add(rid)
    evidence = [_compact_record(item) for item in selected[:14]]

    metric_items = []
    metric_seen = set()
    for topic in topics:
        for item in metrics(snapshot, topic, "", start, end):
            series = str(item.get("series") or "")
            if not series or series in metric_seen or not item.get("current") or not _metric_relevant(question, item):
                continue
            metric_items.append(_compact_metric(item))
            metric_seen.add(series)
    metric_items = metric_items[:12]

    summary = answer.get("summary") if isinstance(answer.get("summary"), dict) else {}
    return {
        "scope": scope,
        "topics": topics,
        "question": question,
        "filters": {"dimension": filters.get("dimension") or "", "start": start, "end": end},
        "answer_summary": str(summary.get("text") or "")[:12000],
        "evidence": evidence,
        "metrics": metric_items,
        "gaps": [str(item)[:400] for item in answer.get("gaps", []) if str(item).strip()][:8],
        "allowed_evidence_ids": [item["id"] for item in evidence],
        "allowed_metric_series": [item["series"] for item in metric_items],
    }


def _plain_markdown(value: str) -> str:
    value = re.sub(r"```.*?```", " ", str(value or ""), flags=re.S)
    value = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[*_`~>#|]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _without_leading_heading(value: str) -> str:
    return re.sub(
        r"^(?:(?:[一二三四五六七八九十]+|\d+)[、.)]\s*)?(?:结论摘要|核心判断|总结回答|结论)\s*[：:]?\s*",
        "",
        value,
    ).strip()


def _fit_complete_sentences(value: str, maximum: int = 300) -> str:
    if len(value) <= maximum:
        return value
    fitted = ""
    for sentence in re.findall(r".*?[。！？；](?:\s+|$)|.+$", value):
        sentence = sentence.strip()
        if len(fitted) + len(sentence) > maximum:
            break
        fitted += sentence
    return fitted if fitted else value[:maximum]


def fallback_business_lead(answer: dict) -> str:
    """从原回答提取业务段落；没有合格段落时使用无新增事实的安全文案。"""
    summary = answer.get("summary") if isinstance(answer.get("summary"), dict) else {}
    raw = str(summary.get("text") or "").replace("\r\n", "\n")
    for block in re.split(r"\n\s*\n", raw):
        clean = _without_leading_heading(_plain_markdown(block))
        clean = re.sub(r"^\d+[.)、]\s*", "", clean)
        sentences = [item.strip() for item in re.findall(r".*?[。！？；](?:\s+|$)|.+$", clean) if item.strip()]
        clean = "".join(item for item in sentences if not _META_OPENING.search(item))
        if not clean:
            continue
        if _BUSINESS_TERMS.search(clean) and len(clean) >= 20:
            return _fit_complete_sentences(clean)
    return "当前材料尚不足以形成不依赖额外推断的业务核心判断。本报告保留原回答、证据与数据缺口，用户应结合最新官方行动、独立信源核验和关键量化指标变化继续判断。"


def _parse_json_object(raw: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S | re.I).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1)
    else:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            text = match.group(0)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("模型未返回对象")
    return value


def _normalize_lead(value: object) -> str:
    text = _plain_markdown(str(value or ""))
    text = _without_leading_heading(text)
    if len(text) <= 300:
        return text
    # A capable model may overshoot the requested length by one final sentence.
    # Keep only complete sentences and never cut a factual claim mid-way.
    fitted = _fit_complete_sentences(text)
    return fitted if len(fitted) >= 150 else text


def _validate_payload(payload: dict, context: dict) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    text = _normalize_lead(payload.get("business_lead"))
    if len(text) < 150 or len(text) > 300 or len(re.findall(r"[\u4e00-\u9fff]", text)) < 90:
        raise ValueError("核心判断长度或中文完整性不符合要求")
    if _META_OPENING.search(text):
        raise ValueError("核心判断以技术元数据开头")
    if not _BUSINESS_TERMS.search(text):
        raise ValueError("核心判断缺少业务方向或触发条件")
    evidence_ids = tuple(dict.fromkeys(str(item) for item in payload.get("evidence_ids", []) if item))
    metric_series = tuple(dict.fromkeys(str(item) for item in payload.get("metric_series", []) if item))
    if not set(evidence_ids).issubset(set(context["allowed_evidence_ids"])):
        raise ValueError("核心判断引用了未授权证据")
    if not set(metric_series).issubset(set(context["allowed_metric_series"])):
        raise ValueError("核心判断引用了未授权指标")
    if payload.get("scope") != context["scope"]:
        raise ValueError("核心判断范围与用户问题不一致")

    allowed_parts = [context["answer_summary"]]
    allowed_parts.extend(json.dumps(item, ensure_ascii=False) for item in context["evidence"] if item["id"] in evidence_ids)
    allowed_parts.extend(json.dumps(item, ensure_ascii=False) for item in context["metrics"] if item["series"] in metric_series)
    unsupported = numeric_tokens(text) - numeric_tokens(" ".join(allowed_parts))
    if unsupported:
        raise ValueError("核心判断包含材料中不存在的数字")
    return text, evidence_ids, metric_series


async def generate_business_lead(
    answer: dict,
    snapshot: dict,
    complete: Callable[..., Awaitable[tuple[str, str]]] | None = None,
) -> BusinessLeadResult:
    """调用当前模型生成业务核心判断；任何异常均返回可安全导出的回退结果。"""
    fallback = fallback_business_lead(answer)
    complete = complete or llm.complete
    try:
        context = build_business_lead_context(answer, snapshot)
    except Exception:
        return BusinessLeadResult(
            fallback,
            _scope_for(answer),
            "fallback",
            warning="业务核心判断生成失败，已使用原回答回退。",
        )
    system = (
        "你是地缘政治研究报告编辑。只生成首页业务核心判断，不重写全文。"
        "必须直接回应用户问题，用一段180至260字中文说明当前状态、关键变化、未来取决于什么；"
        "仅当问题相关时说明大类资产含义。不得以快照、检索轮次、来源等级或证据数量开头，"
        "不得把媒体报道写成已确认行动，不得新增材料中没有的数字。"
        "只返回JSON对象：business_lead、evidence_ids、metric_series、scope。"
    )
    user = "请依据以下有界材料生成核心判断：\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    try:
        raw, _model = await complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model_role="reasoning",
        )
        payload = _parse_json_object(raw)
        text, evidence_ids, metric_series = _validate_payload(payload, context)
        return BusinessLeadResult(text, context["scope"], "model", evidence_ids, metric_series)
    except Exception:
        return BusinessLeadResult(
            fallback,
            context["scope"],
            "fallback",
            warning="业务核心判断生成失败，已使用原回答回退。",
        )
