import asyncio
import json

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
