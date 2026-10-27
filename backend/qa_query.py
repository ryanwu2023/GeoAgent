"""Structured, topic-aware query contracts for the local QA harness.

The public MCP surface intentionally remains string based.  ``FacetQuery`` is
an internal planning/reranking contract which is compiled back to the existing
``q``/``limit``/``source_tiers`` arguments at the tool boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any, Iterable


_ACTION_RE = re.compile(
    r"战争|冲突|停火|和谈|谈判|会谈|协议|斡旋|调解|制裁|封锁|通行|撤离|部署|"
    r"袭击|打击|空袭|导弹|无人机|防空|战线|交付|援助|选举|声明|警告|核设施|"
    r"war|conflict|ceasefire|talks?|negotiat|agreement|mediat|sanction|blockade|"
    r"shipping|deploy|strike|attack|missile|drone|air.?defen|frontline|aid|warning",
    re.I,
)
_MARKET_RE = re.compile(
    r"市场|资产|原油|石油|油价|天然气|黄金|美债|收益率|信用|汇率|股指|航运|保险|"
    r"market|asset|oil|brent|wti|gas|gold|treasur|yield|credit|currency|shipping",
    re.I,
)
_TOPIC_DIRECT = {
    "usiran": re.compile(r"美伊|伊朗|德黑兰|霍尔木兹|胡塞|\biran\w*\b|\btehran\b|\bhormuz\b|\bhouthi\w*\b", re.I),
    "ukraine": re.compile(
        r"俄乌|乌克兰|基辅|泽连斯基|顿涅茨克|顿巴斯|赫尔松|苏梅|别尔哥罗德|"
        r"\bukrain\w*\b|\bkyiv\b|\bzelensk\w*\b|\bdonetsk\b|\bdonbas\w*\b|\bkherson\b",
        re.I,
    ),
}
_TOPIC_BACKGROUND = {
    "usiran": re.compile(r"中东|海湾|美国|以色列|沙特|也门|红海|\bmiddle east\b|\bgulf\b|\bisrael\b|\bsaudi\b", re.I),
    "ukraine": re.compile(r"俄罗斯|莫斯科|普京|欧洲|欧盟|北约|\brussia\w*\b|\bmoscow\b|\bputin\b|\beu\b|\bnato\b", re.I),
}


@dataclass(frozen=True)
class FacetQuery:
    facet: str
    query: str
    must_entities: tuple[str, ...] = ()
    should_terms: tuple[str, ...] = ()
    exclude_entities: tuple[str, ...] = ()
    start: str = ""
    end: str = ""
    preferred_tiers: tuple[str, ...] = ()
    expected_role: str = "support"
    lane: str = "core"
    topic: str = "all"

    @classmethod
    def for_topic(
        cls, topic: str, query: str, *, facet: str = "event", start: str = "", end: str = "",
        expected_role: str = "support", lane: str = "core",
    ) -> "FacetQuery":
        should = {
            "military": ("战线", "打击", "无人机", "导弹", "防空"),
            "diplomacy": ("谈判", "会谈", "斡旋", "协议", "停火"),
            "market": ("原油", "黄金", "美债", "汇率", "信用"),
            "counterevidence": ("否认", "尚未", "条件", "执行", "缓和"),
        }.get(facet, ())
        excludes = ("伊朗", "美伊") if topic == "ukraine" and lane == "core" else ()
        return cls(
            facet=facet, query=query, should_terms=tuple(should), exclude_entities=tuple(excludes),
            start=start, end=end, preferred_tiers=("T1", "T2"), expected_role=expected_role,
            lane=lane, topic=topic,
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("must_entities", "should_terms", "exclude_entities", "preferred_tiers"):
            value[key] = list(value[key])
        return value

    @classmethod
    def from_value(cls, value: "FacetQuery | dict[str, Any]") -> "FacetQuery":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("query spec must be an object")
        data = dict(value)
        for key in ("must_entities", "should_terms", "exclude_entities", "preferred_tiers"):
            raw = data.get(key)
            data[key] = tuple(str(item) for item in raw) if isinstance(raw, (list, tuple)) else ()
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: data[key] for key in data if key in allowed})


def _record_text(record: dict[str, Any]) -> str:
    return " ".join(str(record.get(key) or "") for key in (
        "title", "title_zh", "translated_title", "text", "text_zh", "summary_zh", "translated_text",
    )).lower()


def classify_topic_lane(record: dict[str, Any], query_spec: FacetQuery | dict[str, Any]) -> str:
    """Classify a record as core, cross-topic context, or rejected.

    Topic core admission is intentionally stricter than relevance ranking: a
    record needs a direct topic anchor and an event/policy action.  Market-only
    or broad regional material remains available as cross-topic context.
    """
    spec = FacetQuery.from_value(query_spec)
    text = _record_text(record)
    if any(term.lower() in text for term in spec.exclude_entities if term):
        return "rejected"
    if spec.must_entities and not all(term.lower() in text for term in spec.must_entities if term):
        direct = bool(_TOPIC_DIRECT.get(spec.topic, re.compile(r"$^" )).search(text))
        return "cross_topic" if direct or _MARKET_RE.search(text) else "rejected"
    if spec.topic in {"", "all"}:
        return "core"
    direct_re = _TOPIC_DIRECT.get(spec.topic)
    background_re = _TOPIC_BACKGROUND.get(spec.topic)
    direct = bool(direct_re and direct_re.search(text))
    action = bool(_ACTION_RE.search(text))
    if direct and action:
        return "core"
    if direct or (background_re and background_re.search(text)) or _MARKET_RE.search(text):
        return "cross_topic"
    return "rejected"


def compile_query(query_spec: FacetQuery | dict[str, Any], *, limit: int = 20) -> dict[str, Any]:
    """Compile an internal query into the unchanged public tool arguments."""
    spec = FacetQuery.from_value(query_spec)
    return {"q": spec.query, "limit": max(1, min(20, int(limit)))}


def freshness_windows(question: str, anchor: date) -> list[dict[str, str]]:
    if not re.search(r"最新|今天|今日|近期|当前|目前|现在|进展", question):
        return []
    return [
        {"name": "latest_48h", "start": (anchor - timedelta(days=2)).isoformat(), "end": anchor.isoformat()},
        {"name": "latest_7d", "start": (anchor - timedelta(days=6)).isoformat(), "end": anchor.isoformat()},
        {"name": "background_30d", "start": (anchor - timedelta(days=30)).isoformat(), "end": anchor.isoformat()},
    ]


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def freshness_metadata(records: Iterable[dict[str, Any]], *, as_of: str) -> dict[str, Any]:
    """Describe publication discovery and event freshness on separate clocks."""
    anchor = _timestamp(as_of) or datetime.now(timezone.utc)
    rows = list(records)
    published = [stamp for row in rows if (stamp := _timestamp(row.get("published_at")))]
    high_confidence = []
    for row in rows:
        if str(row.get("tier") or "").upper() not in {"T1", "T2"}:
            continue
        stamp = _timestamp(row.get("event_at")) or _timestamp(row.get("published_at"))
        if stamp:
            high_confidence.append(stamp)
    latest_any = max(published, default=None)
    latest_high = max(high_confidence, default=None)
    stale_warning = ""
    if latest_high is None:
        stale_warning = "最近 72 小时没有可识别的 T1/T2 高可信进展。"
    elif (anchor - latest_high).total_seconds() > 72 * 3600:
        stale_warning = "最近 72 小时没有新增高可信进展；库内较新的发布可能是旧事件转载。"
    return {
        "latest_any_source_at": latest_any.isoformat().replace("+00:00", "Z") if latest_any else None,
        "latest_high_confidence_at": latest_high.isoformat().replace("+00:00", "Z") if latest_high else None,
        "stale_warning": stale_warning,
        "time_basis": "发现按 published_at；事件进展优先按 event_at，缺失时回退 published_at。",
    }
