"""Question-driven metric selection and deterministic path summaries."""
from __future__ import annotations

from datetime import date, datetime
import math
import re
from typing import Any, Iterable


_RUSSIAN_FISCAL = (
    "ru_usdrub_cbr", "usdrub", "brent", "brent_russia_context",
    "ru_brent_rub_proxy", "ru_fiscal_deficit", "ru_oil_gas_revenue",
    "ru_cbr_key_rate",
)
_UKRAINE_AID = (
    "eu_ukraine_facility_disbursement", "eu_ukraine_facility_disbursed_cumulative",
    "ukraine_aid_disbursement", "eu_ukraine_facility_commitment", "ukraine_aid_commitment",
)
_USIRAN_ASSETS = (
    "brent", "hormuz_total", "taco_index", "dgs10", "breakeven10", "hy_oas",
    "gold", "dxy", "djia", "wti", "bab_el_mandeb_total",
)
_UKRAINE_BASE = (
    "ru_usdrub_cbr", "usdrub", "brent", "ru_brent_rub_proxy", "ru_fiscal_deficit",
    "ru_cbr_key_rate", "ttf_gas", "wheat", "eu_ukraine_facility_disbursement",
    "ua_current_account", "ua_goods_balance", "eu_imports_from_ukraine", "eu_exports_to_ukraine",
)


def _series(value: dict[str, Any]) -> str:
    return str(value.get("series") or value.get("id") or "").strip()


def _canonical(series: str) -> str:
    return series.lower().rsplit(":", 1)[-1]


def _matches(series: str, wanted: str) -> bool:
    raw, canonical = series.lower(), _canonical(series)
    return raw == wanted or canonical == wanted or raw.endswith(":" + wanted)


def _question_priority(question: str, topic: str) -> tuple[str, ...]:
    text = str(question or "")
    if re.search(r"俄罗斯|卢布|俄.{0,3}(?:财政|金融|赤字|油气收入)", text) and re.search(
        r"财政|金融|压力|卢布|赤字|油气|利率|收入", text
    ):
        return _RUSSIAN_FISCAL
    if re.search(r"援乌|乌克兰.{0,6}(?:援助|拨款|交付)|Ukraine.{0,8}(?:aid|disbursement)", text, re.I):
        return _UKRAINE_AID
    if topic == "usiran" or re.search(r"美伊|伊朗|霍尔木兹", text):
        return _USIRAN_ASSETS
    if topic == "ukraine":
        return _UKRAINE_BASE
    return ()


def _lexical_score(question: str, item: dict[str, Any]) -> int:
    haystack = f"{_series(item)} {item.get('name') or ''}".lower()
    terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", str(question or "").lower())
    return sum(1 for term in terms if term in haystack)


def select_metric_series(
    question: str,
    topic: str,
    catalog: Iterable[dict[str, Any]],
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Select metrics by business intent rather than catalog position."""
    rows = [dict(item) for item in catalog if isinstance(item, dict) and _series(item)]
    maximum = max(0, min(20, int(limit)))
    if not rows or maximum == 0:
        return []
    priority = _question_priority(question, topic)
    order: dict[str, int] = {}
    for index, wanted in enumerate(priority):
        for item in rows:
            series = _series(item)
            if _matches(series, wanted):
                order.setdefault(series, index)
    # Intent-specific questions should not be padded with unrelated first rows.
    # For generic topic questions lexical matches supplement the base basket.
    selected_pool = [item for item in rows if _series(item) in order]
    if len(selected_pool) < maximum and not priority:
        selected_pool = rows
    elif len(selected_pool) < maximum:
        lexical = [item for item in rows if item not in selected_pool and _lexical_score(question, item) > 0]
        selected_pool.extend(lexical)
    selected_pool.sort(key=lambda item: (
        order.get(_series(item), len(priority) + 100),
        -_lexical_score(question, item),
        0 if item.get("current") is not None else 1,
        _series(item).lower(),
    ))
    return selected_pool[:maximum]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _day(value: Any) -> date | None:
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


def _point(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    stamp = str(value.get("date") or "").strip()
    number = _number(value.get("value"))
    if not stamp or number is None:
        return None
    return {"date": stamp, "value": number}


def _series_row(result: dict[str, Any]) -> dict[str, Any] | None:
    metric = result.get("metric") if isinstance(result.get("metric"), dict) else {}
    series = _series(metric)
    if not series:
        return None
    by_date: dict[str, dict[str, Any]] = {}
    history = (
        result.get("history") if isinstance(result.get("history"), list)
        else metric.get("history") if isinstance(metric.get("history"), list)
        else []
    )
    for value in history:
        point = _point(value)
        if point:
            by_date[point["date"]] = point
    current = _point(metric.get("current"))
    if current:
        by_date[current["date"]] = current
    points = sorted(by_date.values(), key=lambda item: item["date"])
    if not points:
        return {
            "series": series,
            "name": metric.get("name") or series,
            "unit": metric.get("unit"),
            "frequency": metric.get("frequency"),
            "freshness": metric.get("freshness"),
            "first": None, "latest": None, "minimum": None, "maximum": None,
            "observations": 0, "change": None, "change_pct": None,
            "lag_days": None, "scope": metric.get("scope"), "boundary": metric.get("boundary"),
        }
    first, latest = points[0], points[-1]
    minimum = min(points, key=lambda item: item["value"])
    maximum = max(points, key=lambda item: item["value"])
    change = latest["value"] - first["value"]
    change_pct = change / first["value"] * 100 if first["value"] else None
    anchor, observed = _day(result.get("as_of")), _day(latest["date"])
    lag_days = max(0, (anchor - observed).days) if anchor and observed else None
    return {
        "series": series,
        "name": metric.get("name") or series,
        "unit": metric.get("unit"),
        "frequency": metric.get("frequency"),
        "freshness": metric.get("freshness"),
        "first": first,
        "latest": latest,
        "minimum": minimum,
        "maximum": maximum,
        "observations": len(points),
        "change": change,
        "change_pct": change_pct,
        "lag_days": lag_days,
        "scope": metric.get("scope"),
        "boundary": metric.get("boundary"),
    }


def build_metric_block(series_results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build a JSON-safe metric block from tool receipts without unit conversion."""
    rows = [row for value in series_results if isinstance(value, dict) if (row := _series_row(value))]
    return {
        "metrics": rows,
        "basis": "数值、单位和观测日期来自指标工具；区间变化由程序按同一序列首末有效观测计算。",
    }

