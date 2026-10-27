"""Hybrid retrieval and reranking for GeoAgent question answering.

This module deliberately has no dependency on the HTTP or MCP layers.  The
same ranking can therefore be used by the local agent and exposed tools.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
import re
from typing import Any, Iterable

from .qa_query import FacetQuery, classify_topic_lane


ENTITY_ALIASES = {
    "美伊": ("美国", "伊朗", "华盛顿", "德黑兰", "us", "iran"),
    "俄乌": ("俄罗斯", "乌克兰", "莫斯科", "基辅", "russia", "ukraine"),
    "霍尔木兹": ("霍尔木兹", "hormuz", "海峡", "通行", "油轮"),
    "红海": ("红海", "曼德海峡", "胡塞", "houthi", "航运"),
    "外交": ("外交", "谈判", "斡旋", "调解", "停火", "协议", "会谈"),
    "军事": ("军事", "打击", "袭击", "部署", "导弹", "无人机", "防空"),
    "制裁": ("制裁", "财政部", "ofac", "禁运", "限制"),
    "能源": ("原油", "石油", "天然气", "brent", "wti", "能源", "库存"),
    "市场": ("市场", "资产", "黄金", "美债", "收益率", "信用", "汇率", "股指"),
}

STOPWORDS = {
    "什么", "哪些", "如何", "为什么", "是否", "当前", "目前", "现在", "近期", "最近",
    "最新", "情况", "进展", "分析", "总结", "一下", "相关", "以及", "进行", "需要",
}


def _record_text(record: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("title", "title_zh", "translated_title", "text", "text_zh", "summary_zh", "translated_text", "source_name"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    extra = record.get("upstream_extractions")
    if isinstance(extra, list):
        for item in extra:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, dict):
                values.extend(str(value) for value in item.values() if isinstance(value, (str, int, float)))
    return " ".join(values).lower()


def _tokens(text: str) -> list[str]:
    lowered = text.lower()
    result = re.findall(r"[a-z][a-z0-9._-]{1,}|\d{4}-\d{1,2}-\d{1,2}|[\u4e00-\u9fff]{2,}", lowered)
    expanded: list[str] = []
    for token in result:
        if token in STOPWORDS:
            continue
        expanded.append(token)
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", token):
            expanded.extend(token[index:index + 2] for index in range(len(token) - 1))
    return expanded


def _ngrams(text: str, size: int = 2) -> Counter[str]:
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", text.lower())
    if len(compact) < size:
        return Counter([compact]) if compact else Counter()
    return Counter(compact[index:index + size] for index in range(len(compact) - size + 1))


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    common = set(left) & set(right)
    numerator = sum(left[key] * right[key] for key in common)
    denominator = math.sqrt(sum(value * value for value in left.values())) * math.sqrt(sum(value * value for value in right.values()))
    return numerator / denominator if denominator else 0.0


def _published_timestamp(value: Any) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _tier_score(record: dict[str, Any]) -> float:
    tier = str(record.get("tier") or "").upper()
    return {"T1": 1.0, "T2": 0.72, "T3": 0.34}.get(tier, 0.15)


def _verification_score(record: dict[str, Any]) -> float:
    value = str(record.get("verification") or "").lower()
    if value in {"verified", "confirmed", "核验通过", "已核验"}:
        return 1.0
    if value in {"rejected", "false", "已否定"}:
        return -0.5
    return 0.0


def _entity_terms(query: str) -> set[str]:
    lowered = query.lower()
    terms: set[str] = set()
    for anchor, aliases in ENTITY_ALIASES.items():
        if anchor.lower() in lowered or any(alias.lower() in lowered for alias in aliases):
            terms.update(alias.lower() for alias in aliases)
    for value in re.findall(r"[A-Z][A-Za-z.-]{2,}|[\u4e00-\u9fff]{2,8}", query):
        if value not in STOPWORDS:
            terms.add(value.lower())
    return terms


def _origin_key(record: dict[str, Any]) -> tuple[str, str]:
    source = str(record.get("source_id") or record.get("source_name") or "")
    title = re.sub(r"\W+", "", str(record.get("title") or "").lower())[:80]
    return source, title


def _title_key(record: dict[str, Any]) -> str:
    """Return a cross-source duplicate key without hiding the source itself."""
    title = str(
        record.get("title_zh")
        or record.get("translated_title")
        or record.get("title")
        or ""
    ).lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title)[:120]


def hybrid_retrieve(
    records: Iterable[dict[str, Any]],
    query: str,
    *,
    limit: int = 20,
    recent_after: str = "",
    source_tiers: set[str] | None = None,
    query_spec: FacetQuery | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return a diverse, reranked result set.

    Relevance always dominates freshness and source quality, preventing a new
    but unrelated item from displacing a direct report.  Source tier is a
    ranking signal only and never treated as fact verification.
    """
    spec = FacetQuery.from_value(query_spec) if query_spec is not None else None
    pool = [
        record for record in records
        if (not recent_after or str(record.get("published_at") or "")[:10] >= recent_after)
        and (not source_tiers or str(record.get("tier") or "").upper() in source_tiers)
        and (not spec or not spec.start or str(record.get("published_at") or "")[:10] >= spec.start)
        and (not spec or not spec.end or str(record.get("published_at") or "")[:10] <= spec.end)
        and (
            not spec
            or (spec.lane == "any" and classify_topic_lane(record, spec) != "rejected")
            or classify_topic_lane(record, spec) == spec.lane
        )
    ]
    if not pool or limit <= 0:
        return []

    query_tokens = _tokens(query)
    ascii_only_query = bool(query_tokens) and all(re.fullmatch(r"[a-z][a-z0-9._-]*", token) for token in query_tokens)
    query_counts = Counter(query_tokens)
    query_grams = _ngrams(query)
    entities = _entity_terms(query)
    documents = [_record_text(record) for record in pool]
    document_tokens = [_tokens(text) for text in documents]
    document_frequency: Counter[str] = Counter()
    for tokens in document_tokens:
        document_frequency.update(set(tokens))
    average_length = sum(len(tokens) for tokens in document_tokens) / max(1, len(document_tokens))
    newest = max((_published_timestamp(record.get("published_at")) for record in pool), default=0.0)
    oldest = min((_published_timestamp(record.get("published_at")) for record in pool if _published_timestamp(record.get("published_at"))), default=newest)
    recent_intent = bool(re.search(r"最新|近期|最近|当前|目前|今天|今日|进展", query))

    scored: list[tuple[float, float, dict[str, Any]]] = []
    total = len(pool)
    for record, text, tokens in zip(pool, documents, document_tokens):
        token_counts = Counter(tokens)
        title = str(record.get("title") or "").lower()
        exact = 0.0
        bm25 = 0.0
        for token, query_weight in query_counts.items():
            frequency = token_counts.get(token, 0)
            if not frequency:
                continue
            exact += query_weight * (3.0 if token in title else 1.0)
            inverse = math.log(1 + (total - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
            normalizer = frequency + 1.2 * (0.25 + 0.75 * len(tokens) / max(1.0, average_length))
            bm25 += inverse * frequency * 2.2 / normalizer
        semantic = _cosine(query_grams, _ngrams(text))
        entity_hits = sum(1 for entity in entities if entity and entity in text)
        if ascii_only_query and exact == 0 and entity_hits == 0:
            continue
        relevance = exact + 2.4 * bm25 + 7.0 * semantic + 1.8 * entity_hits
        if relevance <= 0.05:
            continue
        published = _published_timestamp(record.get("published_at"))
        recency = (published - oldest) / max(1.0, newest - oldest) if published and newest > oldest else 0.0
        quality = _tier_score(record) + 0.4 * _verification_score(record)
        if spec and str(record.get("tier") or "").upper() in spec.preferred_tiers:
            quality += 0.4
        if spec:
            quality += 0.18 * sum(term.lower() in text for term in spec.should_terms if term)
        completeness = min(1.0, len(str(record.get("text") or "")) / 1200)
        # A query asking for the latest situation must still be relevant first,
        # but a near-equivalent recent report should outrank an older one.
        # The caller already applies the requested date window; this only
        # orders records inside that window.
        recency_weight = 8.0 if recent_intent else 1.5
        score = relevance * 10.0 + recency_weight * recency + quality + 0.35 * completeness
        if recent_intent and published and newest:
            age_days = max(0.0, (newest - published) / 86400.0)
            # Decay the whole relevance score rather than blindly placing any
            # new mention ahead of a materially relevant older report. A
            # recent but weakly related item therefore cannot win on date
            # alone, while a near-equivalent fresh report usually leads.
            freshness = math.exp(-age_days / 7.0)
            score *= 0.08 + 0.92 * freshness
        scored.append((score, published, record))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected: list[dict[str, Any]] = []
    seen_exact: set[tuple[str, str]] = set()
    seen_titles: set[str] = set()
    source_counts: Counter[str] = Counter()
    for _, _, record in scored:
        key = _origin_key(record)
        title_key = _title_key(record)
        if key in seen_exact or (title_key and title_key in seen_titles):
            continue
        source = key[0]
        # Preserve source diversity while still allowing two genuinely distinct
        # reports from an authoritative source in a wider result set.
        if source and source_counts[source] >= (2 if limit >= 12 else 1):
            continue
        seen_exact.add(key)
        if title_key:
            seen_titles.add(title_key)
        source_counts[source] += 1
        selected.append(record)
        if len(selected) >= limit:
            break
    return selected


def evidence_budget(query: str, *, topic: str = "all") -> dict[str, int]:
    complex_terms = ("分析", "研判", "进展", "局势", "总结", "简报", "影响", "风险", "展望", "走势", "对比", "资产", "情景", "反证")
    current_situation_request = bool(re.search(r"(?:最新|近期|当前|目前|现在).{0,8}(?:情况|动态|局势|进展)", query))
    complex_question = current_situation_request or any(term in query for term in complex_terms)
    broad = topic == "all" or any(term in query for term in ("美伊和俄乌", "跨主题", "全球", "大类资产"))
    deep_research = complex_question and any(
        term in query for term in ("详细", "全面", "深度", "依据", "证据", "复核", "传导", "配置", "报告")
    )
    if broad and complex_question:
        evidence = 96
    elif deep_research or current_situation_request:
        evidence = 80
    elif complex_question:
        evidence = 64
    else:
        evidence = 24
    return {
        "evidence": evidence,
        "search_page": 20 if complex_question else 12,
        "full_text": 24 if broad and complex_question else 20 if deep_research else 16 if complex_question else 8,
        "internal_messages": 20 if complex_question else 10,
        "internal_full_text": 6 if complex_question else 3,
        "tool_calls": 18 if broad and complex_question else 16 if deep_research else 14 if complex_question else 9,
    }
