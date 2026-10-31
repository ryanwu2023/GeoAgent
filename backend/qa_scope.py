"""本地智能问答的运行级研究范围契约。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from .qa_harness_state import HarnessScope


_USIRAN = re.compile(
    r"美伊|美国.{0,8}伊朗|伊朗.{0,8}美国|霍尔木兹|胡塞|"
    r"\bIran(?:ian)?\b|\bTehran\b|\bHormuz\b|\bHouthi\w*\b",
    re.I,
)
_UKRAINE = re.compile(
    r"俄乌|俄罗斯.{0,8}乌克兰|乌克兰.{0,8}俄罗斯|乌克兰战争|"
    r"\bUkraine\b.{0,20}\bRussia(?:n)?\b|\bRussia(?:n)?\b.{0,20}\bUkraine\b",
    re.I,
)


def _instant(value: str, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _day(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _relative_days(question: str, default: int) -> int:
    match = re.search(r"(?:最近|近)\s*(\d{1,3}|[一二两三四五六七八九十])\s*(天|周|个月|月)", question)
    if not match:
        return default
    names = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    amount = int(match.group(1)) if match.group(1).isdigit() else names[match.group(1)]
    return max(1, min(365, amount if match.group(2) == "天" else amount * 7 if match.group(2) == "周" else amount * 30))


@dataclass(frozen=True)
class ResearchScope:
    original_question: str
    topics: tuple[str, ...]
    primary_topic: str
    topic_source: str
    dimension: str
    view_scope: HarnessScope
    snapshot_id: str
    snapshot_created_at: str
    data_as_of: str
    run_started_at: str
    deadline_at: str
    timezone: str
    recent_start: str
    recent_end: str
    background_start: str
    background_end: str
    source_permissions: tuple[str, ...] = ("public", "internal", "external_mcp")
    version: str = "research-scope/v1"

    @staticmethod
    def question_topics(question: str) -> tuple[str, ...]:
        topics = []
        if _USIRAN.search(str(question or "")):
            topics.append("usiran")
        if _UKRAINE.search(str(question or "")):
            topics.append("ukraine")
        return tuple(topics)

    @classmethod
    def from_question(
        cls, question: str, *, view_scope: HarnessScope, snapshot_created_at: str,
        now: datetime, deadline_seconds: float = 300, timezone_name: str = "Asia/Shanghai",
    ) -> "ResearchScope":
        started = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        started_utc = started.astimezone(timezone.utc)
        explicit = cls.question_topics(question)
        if explicit:
            topics = explicit
            topic_source = "user_explicit"
        elif view_scope.topic in {"usiran", "ukraine"}:
            topics = (view_scope.topic,)
            topic_source = "dashboard"
        else:
            topics = ("usiran", "ukraine")
            topic_source = "dashboard_all"
        primary = topics[0] if len(topics) == 1 else "all"
        dimension = view_scope.dimension if primary == view_scope.topic else ""

        snapshot_time = _instant(snapshot_created_at, started_utc)
        end_day = min(started_utc.date(), snapshot_time.date())
        view_end = _day(view_scope.end)
        if view_end:
            end_day = min(end_day, view_end)
        latest = bool(re.search(r"最新|今天|今日|近期|当前|目前|现在|进展", question))
        recent_days = _relative_days(question, 7 if latest else 30)
        view_start = _day(view_scope.start)
        recent_start = end_day - timedelta(days=max(0, recent_days - 1))
        if view_start:
            recent_start = max(recent_start, view_start)
        background_days = max(90, recent_days)
        background_start = view_start or (end_day - timedelta(days=background_days - 1))
        return cls(
            original_question=str(question), topics=topics, primary_topic=primary,
            topic_source=topic_source, dimension=dimension, view_scope=view_scope,
            snapshot_id=view_scope.snapshot_id, snapshot_created_at=str(snapshot_created_at or ""),
            data_as_of=end_day.isoformat(), run_started_at=started_utc.isoformat(),
            deadline_at=(started_utc + timedelta(seconds=max(1.0, float(deadline_seconds)))).isoformat(),
            timezone=timezone_name, recent_start=recent_start.isoformat(), recent_end=end_day.isoformat(),
            background_start=background_start.isoformat(), background_end=end_day.isoformat(),
        )

    def validate_arguments(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        value = dict(arguments or {})
        requested_topic = str(value.get("topic") or "")
        allowed_topics = set(self.topics)
        if self.primary_topic == "all":
            allowed_topics.add("all")
        if requested_topic and requested_topic not in allowed_topics:
            raise ValueError(f"工具 {tool} 试图越过本轮研究主题范围")
        start, end = _day(str(value.get("start") or "")), _day(str(value.get("end") or ""))
        lower, upper = date.fromisoformat(self.background_start), date.fromisoformat(self.background_end)
        if start and start < lower:
            raise ValueError(f"工具 {tool} 的开始日期早于已批准背景窗口")
        if end and end > upper:
            raise ValueError(f"工具 {tool} 的结束日期晚于资料截至时间")
        if start and end and start > end:
            raise ValueError(f"工具 {tool} 的日期范围无效")
        return value

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["topics"] = list(self.topics)
        value["source_permissions"] = list(self.source_permissions)
        value["scope_hash"] = self.stable_hash()
        return value

    def cache_scope(self) -> dict[str, Any]:
        return {
            "version": self.version, "scope_hash": self.stable_hash(),
            "snapshot_id": self.snapshot_id, "topics": list(self.topics),
            "dimension": self.dimension,
            "recent": {"start": self.recent_start, "end": self.recent_end},
            "background": {"start": self.background_start, "end": self.background_end},
            "source_permissions": list(self.source_permissions),
        }

    def stable_hash(self) -> str:
        payload = {
            "version": self.version, "question": self.original_question,
            "topics": self.topics, "topic_source": self.topic_source, "dimension": self.dimension,
            "snapshot_id": self.snapshot_id, "data_as_of": self.data_as_of,
            "recent": (self.recent_start, self.recent_end),
            "background": (self.background_start, self.background_end),
            "permissions": self.source_permissions,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
