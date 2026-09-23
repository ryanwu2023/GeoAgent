"""Adapter for the repository-local TACO market pressure monitor."""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path


SERIES = {
    "USGG10YR": ("美国10年期国债收益率", "%", "FRED DGS10 原始水平"),
    "USSWIT1_proxy_T5YIE": ("美国5年期盈亏平衡通胀率代理", "%", "FRED T5YIE；作为原模型通胀互换的公开代理"),
    "RCPPTAPP_approve": ("美国总统支持率聚合", "%", "Silver Bulletin / Datawrapper 聚合值"),
    "INDU": ("道琼斯工业指数", "点", "公开市场指数水平"),
    "CO1_Brent": ("布伦特原油期货", "美元/桶", "ICE首月连续合约公开行情"),
    "TACO_Index_T": ("TACO资产压力指数", "z", "5 因子等权 z 分数合成后取7日均线"),
    "TRHBCCCD_n_total": ("霍尔木兹海峡日通行艘次", "艘次", "IMF PortWatch 观察序列"),
}


def _number(value: str | None) -> float | None:
    try:
        number = float(value) if value not in (None, "") else None
        return number if number is not None and math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def load_taco(root: Path, records: dict, observations: list, sources: list, read=None) -> None:
    output = root / "taco-monitor" / "output"
    csv_file = output / "taco-latest.csv"
    if not csv_file.is_file():
        sources.append({"id": "taco-monitor", "monitor": "taco-monitor", "name": "TACO资产压力监测", "state": "not_connected", "count": 0, "latest": None})
        return
    csv_text = read(csv_file) if read else csv_file.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    status_file = output / "_source_status.json"
    status = {}
    if status_file.is_file():
        status_text = read(status_file) if read else status_file.read_text(encoding="utf-8-sig")
        status = json.loads(status_text)
    status_rows = {item.get("id"): item for item in status.get("statuses", [])}
    source_id = "taco-methodology"
    records[source_id] = {
        "id": source_id,
        "source_id": "taco-monitor",
        "source_name": "TACO资产压力监测",
        "tier": "研究方法",
        "publisher": "项目内公开数据合成",
        "title": "TACO压力指数及公开输入序列",
        "text": "五项公开输入经标准化等权合成；指数用于观察资产压力，不是事件概率或收益预测。",
        "url": "",
        "published_at": status.get("run_date"),
        "fetched_at": status.get("run_date"),
        "first_seen": status.get("run_date"),
        "event_at": None,
        "party": "market",
        "nature": "结构化指标",
        "verification": "计算口径已导入，来源状态逐项保留",
        "classification": "市场背景",
        "topics": ["usiran"],
        "dimensions": {"usiran": "financial"},
        "dimension_candidates": {"usiran": ["financial"]},
        "scope": "跨资产市场背景",
        "points": [],
        "origin_id": "taco-monitor",
        "adapters": ["taco-monitor"],
        "raw_refs": [],
        "upstream_extractions": [],
    }
    for column, (name, unit, method) in SERIES.items():
        history = [
            {"date": row["Date"], "value": value}
            for row in rows
            if (value := _number(row.get(column))) is not None
        ]
        if not history:
            continue
        is_hormuz = column == "TRHBCCCD_n_total"
        series_id = "HORMUZ_TRANSIT" if is_hormuz else "TACO_INDEX" if column == "TACO_Index_T" else column
        boundary = (
            "观察指标；不进入 TACO 五因子合成。真实观测可能滞后，前值填充不能视为当日新增观测。"
            if is_hormuz
            else "市场同步变化不单独证明由冲突造成；TACO 不是概率、价格预测或配置权重。"
        )
        observations.append({
            "id": "taco:" + series_id,
            "series": series_id,
            "name": name,
            "type": "quantitative",
            "unit": unit,
            "frequency": "日频",
            "scope": "霍尔木兹海峡" if is_hormuz else "跨资产市场背景",
            "topic": "usiran",
            "dimension": "financial",
            "method": method,
            "boundary": boundary,
            "source_ids": [source_id],
            "history": history,
            "observed_at": history[-1]["date"],
            "bucket": "shipping" if is_hormuz else "market",
            "role": "observe" if is_hormuz else "index_input" if column != "TACO_Index_T" else "composite",
        })
    sources.append({
        "id": "taco-monitor",
        "monitor": "taco-monitor",
        "name": "TACO资产压力监测",
        "state": "imported" if rows else "ok-empty",
        "count": len(rows),
        "latest": rows[-1].get("Date") if rows else None,
        "checked_at": status.get("run_date"),
        "upstream": [{"id": key, "status": value.get("status"), "url": value.get("url")} for key, value in status_rows.items()],
    })

