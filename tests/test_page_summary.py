import asyncio
import json

import pytest

from backend.adapters import normalize
from backend.research import page_summary
from backend.store import get_snapshot, save_snapshot


def _snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    get_snapshot.cache_clear()
    record = normalize(
        {
            "id": "page-summary-source",
            "title": "Iran supply update",
            "summary": "Public source reports a supply change.",
            "link": "https://example.org/supply",
            "published": "2026-10-16T08:00:00Z",
            "source_id": "official",
            "party": "iran",
        },
        "readiness-monitor",
    )
    payload = {
        "records": [record],
        "observations": [{
            "id": "spend", "series": "spend", "name": "采购", "unit": "美元",
            "scope": "测试范围", "method": "公开数据", "boundary": "仅用于测试",
            "frequency": "月度", "topic": "usiran", "dimension": "us_readiness",
            "source_ids": [record["id"]],
            "history": [{"date": "2026-09-30", "value": 100}, {"date": "2026-10-16", "value": 110}],
        }],
        "sources": [], "manifest": [], "errors": [],
    }
    sid, _ = save_snapshot(payload)
    return get_snapshot(sid), record["id"]


def test_page_summary_calls_model_once_and_reuses_snapshot_cache(tmp_path, monkeypatch):
    snapshot, record_id = _snapshot(tmp_path, monkeypatch)
    calls = []

    async def fake_complete(messages):
        calls.append(messages)
        return json.dumps({
            "text": "采购由100美元上升至110美元，说明当前观测出现变化，但仍需结合来源边界判断。",
            "evidence_ids": [record_id],
        }, ensure_ascii=False), "test-model"

    monkeypatch.setattr("backend.research.model_status", lambda: {"configured": True, "model": "test-model"})
    monkeypatch.setattr("backend.research.llm.complete", fake_complete)

    first = asyncio.run(page_summary(snapshot, "metrics", "usiran"))
    second = asyncio.run(page_summary(snapshot, "metrics", "usiran"))

    assert first["status"] == "ready"
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["text"] == first["text"]
    assert len(calls) == 1


def test_page_summary_keeps_program_view_when_model_is_unavailable(tmp_path, monkeypatch):
    snapshot, _ = _snapshot(tmp_path, monkeypatch)
    monkeypatch.setattr("backend.research.model_status", lambda: {"configured": False, "model": "未配置"})

    result = asyncio.run(page_summary(snapshot, "assets", "usiran"))

    assert result["status"] == "unavailable"
    assert result["text"] is None
    assert "程序计算" in result["message"]


def _metric_case(tmp_path, monkeypatch, *, current, previous, unit="USD"):
    snapshot, record_id = _snapshot(tmp_path, monkeypatch)
    snapshot["observations"][0].update({
        "unit": unit,
        "history": [
            {"date": "2026-09-30", "value": previous},
            {"date": "2026-10-16", "value": current},
        ],
    })
    monkeypatch.setattr("backend.research.model_status", lambda: {"configured": True, "model": "test-model"})
    return snapshot, record_id


@pytest.mark.parametrize("page", ["metrics", "assets", "scenarios"])
def test_page_summary_accepts_cited_program_display_and_percentages(tmp_path, monkeypatch, page):
    snapshot, record_id = _metric_case(
        tmp_path, monkeypatch, current=67923317481.69, previous=86049198421.17,
    )
    contexts = []

    async def complete(messages):
        contexts.append(json.loads(messages[1]["content"]))
        return json.dumps({
            "text": "采购为679.23亿美元，较前值减少181.26亿美元、下降21.06%；这是程序计算，不证明冲突因果。",
            "evidence_ids": [record_id],
        }, ensure_ascii=False), "test-model"

    monkeypatch.setattr("backend.research.llm.complete", complete)
    result = asyncio.run(page_summary(snapshot, page, "usiran"))

    assert result["status"] == "ready", result["message"]
    metric = contexts[0]["metrics"][0]
    assert metric["current"]["value"] == 67923317481.69
    assert metric["display"]["current"]["values"] == ["679.23亿美元", "679.2亿美元"]
    assert metric["display"]["pct"] == ["-21.06%（减少，幅度21.06%）", "-21.1%（减少，幅度21.1%）"]
    assert "-181.26亿美元" in metric["display"]["delta"][0]


@pytest.mark.parametrize("text,expected", [
    ("当前为95.74美元/桶，下降6.68%。", "ready"),
    ("当前为95.75美元/桶，下降6.68%。", "failed"),
    ("当前为95.74美元/桶，下降6.69%。", "failed"),
])
def test_page_summary_rounding_is_explicit_not_fuzzy(tmp_path, monkeypatch, text, expected):
    snapshot, record_id = _metric_case(
        tmp_path, monkeypatch, current=95.73999786376953,
        previous=102.58999633789062, unit="美元/桶",
    )

    async def complete(messages):
        return json.dumps({"text": text, "evidence_ids": [record_id]}, ensure_ascii=False), "test-model"

    monkeypatch.setattr("backend.research.llm.complete", complete)
    result = asyncio.run(page_summary(snapshot, "metrics", "usiran"))

    assert result["status"] == expected, result["message"]
    if expected == "failed":
        assert result["text"] is None
        assert "引用材料中不存在的数值" in result["message"]


def test_page_summary_metric_display_still_requires_its_source(tmp_path, monkeypatch):
    snapshot, record_id = _metric_case(
        tmp_path, monkeypatch, current=14765526933.65, previous=13817420012.75,
    )
    other = {**snapshot["records"][0], "id": "unrelated-source"}
    snapshot["records"].append(other)

    async def complete(messages):
        return json.dumps({
            "text": "采购为147.66亿美元，增长6.86%。",
            "evidence_ids": ["unrelated-source"],
        }, ensure_ascii=False), "test-model"

    monkeypatch.setattr("backend.research.llm.complete", complete)
    result = asyncio.run(page_summary(snapshot, "metrics", "usiran"))

    assert record_id != "unrelated-source"
    assert result["status"] == "failed"
    assert "147.66" in result["message"]


def test_page_summary_small_numbers_support_display_precision(tmp_path, monkeypatch):
    snapshot, record_id = _metric_case(
        tmp_path, monkeypatch, current=3.142857142857143, previous=3.0, unit="艘次",
    )

    async def complete(messages):
        return json.dumps({
            "text": "日均通行为3.14艘次（较精确展示为3.1429艘次），增长4.76%。",
            "evidence_ids": [record_id],
        }, ensure_ascii=False), "test-model"

    monkeypatch.setattr("backend.research.llm.complete", complete)
    result = asyncio.run(page_summary(snapshot, "metrics", "usiran"))

    assert result["status"] == "ready", result["message"]


@pytest.mark.parametrize("text,expected", [
    ("采购为679.2亿美元，前值860.5亿美元，下降21.1%。", "ready"),
    ("采购为679.3亿美元，前值860.5亿美元，下降21.1%。", "failed"),
    ("采购为679.2亿美元，前值860.5亿美元，下降21.2%。", "failed"),
])
def test_page_summary_one_decimal_display_is_rounded_not_truncated(tmp_path, monkeypatch, text, expected):
    snapshot, record_id = _metric_case(
        tmp_path, monkeypatch, current=67923317481.69, previous=86049198421.17,
    )

    async def complete(messages):
        return json.dumps({"text": text, "evidence_ids": [record_id]}, ensure_ascii=False), "test-model"

    monkeypatch.setattr("backend.research.llm.complete", complete)
    result = asyncio.run(page_summary(snapshot, "metrics", "usiran"))
    assert result["status"] == expected, result["message"]


@pytest.mark.parametrize("amount,expected", [("97.79", "ready"), ("977.88", "failed")])
def test_page_summary_does_not_accept_tenfold_currency_conversion(tmp_path, monkeypatch, amount, expected):
    snapshot, record_id = _metric_case(
        tmp_path, monkeypatch, current=9778765264.93, previous=22415330581.84,
    )

    async def complete(messages):
        return json.dumps({
            "text": f"军事人员支出为{amount}亿美元，下降56.37%。",
            "evidence_ids": [record_id],
        }, ensure_ascii=False), "test-model"

    monkeypatch.setattr("backend.research.llm.complete", complete)
    result = asyncio.run(page_summary(snapshot, "metrics", "usiran"))

    assert result["status"] == expected, result["message"]


def test_page_summary_does_not_authorize_numbers_hidden_by_excerpt(tmp_path, monkeypatch):
    snapshot, record_id = _snapshot(tmp_path, monkeypatch)
    snapshot["records"][0]["text"] = "公开来源。" * 300 + "隐去的价格为9876.54美元。"
    monkeypatch.setattr("backend.research.model_status", lambda: {"configured": True, "model": "test-model"})

    async def complete(messages):
        context = json.loads(messages[1]["content"])
        assert "9876.54" not in context["evidence"][0]["excerpt"]
        return json.dumps({
            "text": "价格为9876.54美元。", "evidence_ids": [record_id],
        }, ensure_ascii=False), "test-model"

    monkeypatch.setattr("backend.research.llm.complete", complete)
    result = asyncio.run(page_summary(snapshot, "metrics", "usiran"))

    assert result["status"] == "failed"
    assert "9876.54" in result["message"]
