"""Evidence coverage gate for the local QA research harness."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable


_FOCUS_FACETS = {
    "事件主线": "event",
    "外交": "diplomacy",
    "军事": "military",
    "市场与资产": "market",
    "反证与不确定性": "counterevidence",
    "后续观察": "counterevidence",
}
_TERMINAL = {"full_text_supported", "conflicted", "gap_confirmed", "budget_exhausted"}


@dataclass(frozen=True)
class CoverageState:
    facet: str
    status: str
    evidence_ids: tuple[str, ...] = ()
    full_text_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    forced: bool
    coverage: dict[str, CoverageState]
    missing_facets: tuple[str, ...] = ()
    follow_up_tasks: tuple[str, ...] = ()
    stale_warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "forced": self.forced,
            "coverage": {
                key: {
                    "facet": value.facet,
                    "status": value.status,
                    "evidence_ids": list(value.evidence_ids),
                    "full_text_ids": list(value.full_text_ids),
                    "reason": value.reason,
                }
                for key, value in self.coverage.items()
            },
            "missing_facets": list(self.missing_facets),
            "follow_up_tasks": list(self.follow_up_tasks),
            "stale_warning": self.stale_warning,
        }


def _entries(ledger: Any) -> list[dict[str, Any]]:
    if hasattr(ledger, "to_dict"):
        ledger = ledger.to_dict()
    if isinstance(ledger, dict):
        values = ledger.get("entries", [])
        if isinstance(values, dict):
            values = list(values.values())
        return [item for item in values if isinstance(item, dict)]
    return []


def _required_facets(plan: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for focus in plan.get("focuses", []):
        facet = _FOCUS_FACETS.get(str(focus))
        if facet and facet not in result:
            result.append(facet)
    return result or ["event"]


def _parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _stale_warning(plan: dict[str, Any], tool_state: dict[str, Any]) -> str:
    question = str(plan.get("question") or "")
    if not any(term in question for term in ("最新", "今天", "今日", "当前", "近期", "进展")):
        return ""
    newest = _parse_day(tool_state.get("latest_high_confidence_at"))
    anchor = _parse_day(tool_state.get("answer_as_of"))
    if newest and anchor and (anchor - newest).days >= 3:
        return "最近 72 小时没有新增高可信材料；以下结论基于当前库内较早记录。"
    if not newest and "48h" in set(tool_state.get("checked_windows") or []):
        return "最近 72 小时未发现可确认的高可信新增材料。"
    return ""


def evaluate_readiness(
    plan: dict[str, Any],
    ledger: Any,
    tool_state: dict[str, Any],
    budget: dict[str, int],
) -> ReadinessReport:
    required = _required_facets(plan)
    values = _entries(ledger)
    searched = {str(item) for item in tool_state.get("searched_facets", [])}
    used = int(budget.get("used", 0) or 0)
    maximum = max(0, int(budget.get("maximum", 0) or 0))
    exhausted = maximum > 0 and used >= maximum
    coverage: dict[str, CoverageState] = {}
    follow_up: list[str] = []

    for facet in required:
        related = [item for item in values if str(item.get("facet") or "") == facet]
        evidence_ids = tuple(dict.fromkeys(
            str(rid) for item in related for rid in item.get("supporting_ids", []) if str(rid)
        ))
        full_text_ids = tuple(dict.fromkeys(
            str(rid) for item in related for rid in item.get("full_text_ids", []) if str(rid)
        ))
        has_conflict = any(item.get("conflicting_ids") or item.get("status") == "conflicted" for item in related)
        has_gap = any(item.get("status") == "gap" for item in related)
        if has_conflict:
            status, reason = "conflicted", "已取得相互冲突的材料，最终回答必须并列说明。"
        elif full_text_ids:
            status, reason = "full_text_supported", "至少一条相关主张已读取完整正文。"
        elif has_gap and facet in searched:
            status, reason = "gap_confirmed", "已检索该分面，但当前范围内仍缺少充分依据。"
        elif exhausted:
            status, reason = "budget_exhausted", "工具预算已用尽，按缺口降级收敛。"
        elif facet in searched:
            status, reason = "searched", "已有候选线索，但尚无完整正文支持。"
        else:
            status, reason = "unstarted", "尚未检索该研究分面。"
        coverage[facet] = CoverageState(facet, status, evidence_ids, full_text_ids, reason)
        if status not in _TERMINAL:
            follow_up.append(f"补查 {facet}：检索并读取至少一条关键正文。")

    latest_question = any(term in str(plan.get("question") or "") for term in ("最新", "今天", "今日", "当前", "近期", "进展"))
    checked_windows = set(tool_state.get("checked_windows") or [])
    if latest_question and not {"48h", "7d"}.issubset(checked_windows) and not exhausted:
        follow_up.append("补查最新时间层：完成最近 48 小时与最近 7 天检索。")

    needs_counter = "counterevidence" in required
    if needs_counter and not tool_state.get("counter_search_done") and not exhausted:
        follow_up.append("补查反证：搜索相反报道、来源冲突和未执行条件。")

    if plan.get("needs_metrics") and not tool_state.get("metric_series") and not exhausted:
        follow_up.append("补查指标：读取与用户问题匹配的量化指标序列。")

    missing = tuple(facet for facet, item in coverage.items() if item.status not in _TERMINAL)
    forced = exhausted and any(item.status == "budget_exhausted" for item in coverage.values())
    ready = not missing and not follow_up
    if exhausted:
        ready = True
        forced = bool(forced or missing or follow_up)
    return ReadinessReport(
        ready=ready,
        forced=forced,
        coverage=coverage,
        missing_facets=missing,
        follow_up_tasks=tuple(dict.fromkeys(follow_up)),
        stale_warning=_stale_warning(plan, tool_state),
    )


def select_faceted_reads(
    records: Iterable[dict[str, Any]],
    required_facets: Iterable[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Reserve one read per facet before filling remaining slots globally."""
    values = [item for item in records if isinstance(item, dict) and item.get("id")]
    values.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for facet in dict.fromkeys(str(item) for item in required_facets if str(item)):
        candidate = next((item for item in values if str(item.get("facet") or "") == facet and str(item["id"]) not in seen), None)
        if candidate is None:
            continue
        selected.append(candidate)
        seen.add(str(candidate["id"]))
        if len(selected) >= max(0, limit):
            return selected
    for item in values:
        rid = str(item["id"])
        if rid in seen:
            continue
        selected.append(item)
        seen.add(rid)
        if len(selected) >= max(0, limit):
            break
    return selected

