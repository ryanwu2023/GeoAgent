import json

from backend import chinese


def _answer():
    record = {
        "id": "evidence-1",
        "title": "伊朗局势最新进展",
        "text": "完整证据正文" * 4000,
        "source_name": "Reuters",
        "publisher": "Reuters",
        "published_at": "2026-10-24T01:00:00Z",
        "fetched_at": "2026-10-24T01:05:00Z",
        "tier": "T2",
        "verification": "待交叉核验",
        "url": "https://example.com/news",
        "topics": ["usiran"],
        "dimensions": {"usiran": ["diplomacy"]},
        "points": [],
        "raw_refs": ["full-original-record" * 500],
    }
    return {
        "id": "answer-1",
        "snapshot_id": "snapshot-1",
        "query": "最新进展",
        "facts": [{"text": "10 USD", "evidence_ids": ["evidence-1"], "kind": "程序计算"}],
        "citations": [record],
        "internal_sources": [{
            "id": "internal-1", "title": "内部线索", "ai_content": "内部消息全文" * 4000,
            "message_at": "2026-10-24T02:00:00+08:00",
        }],
        "question_plan": {"large": "plan" * 2000},
        "tool_calls": [{"tool": "geo_evidence_get", "arguments": {"body": "x" * 8000}}],
        "analysis_trace": [
            {
                "round": 1, "stage": "reading", "title": "全文读取", "status": "completed",
                "tool": "geo_evidence_get", "result_count": 8,
                "summary": "中间研究摘要" * 300,
                "details": ["复核问题" * 300], "search_queries": ["霍尔木兹 通行"],
                "evidence_ids": ["evidence-1"],
            },
            {
                "round": 2, "stage": "synthesis", "title": "最终复核与结论", "status": "completed",
                "summary": "最终答案正文" * 2000, "details": [], "search_queries": [],
                "evidence_ids": ["evidence-1"],
            },
        ],
        "summary": {"text": "最终答案正文" * 2000, "evidence_ids": ["evidence-1"]},
    }


def test_compact_answer_keeps_ui_metadata_and_drops_full_bodies(monkeypatch):
    monkeypatch.setattr(chinese, "translation_cache", lambda: {})
    answer = _answer()
    compact = chinese.present_answer(answer, compact=True)

    assert compact["response_projection"] == "compact-v1"
    assert compact["citations"] == [{
        "id": "evidence-1",
        "title": "伊朗局势最新进展",
        "source_name": "路透社",
        "publisher": "Reuters",
        "published_at": "2026-10-24T01:00:00Z",
        "fetched_at": "2026-10-24T01:05:00Z",
        "tier": "T2",
        "verification": "待交叉核验",
    }]
    assert compact["internal_sources"][0]["content_abstract"].endswith("…")
    assert "ai_content" not in compact["internal_sources"][0]
    assert "question_plan" not in compact and "tool_calls" not in compact
    assert compact["facts"][0]["text"] == "10 美元"
    assert compact["analysis_trace"][0]["tool"] == "geo_evidence_get"
    assert compact["analysis_trace"][0]["result_count"] == 8
    assert compact["analysis_trace"][0]["search_queries"] == ["霍尔木兹 通行"]
    assert compact["analysis_trace"][0]["evidence_ids"] == ["evidence-1"]
    assert compact["analysis_trace"][0]["summary"].endswith("…")
    assert compact["analysis_trace"][0]["details"][0].endswith("…")
    assert compact["analysis_trace"][1]["summary"] == "已完成最终复核与结论，完整内容见下方总结回答。"
    assert compact["summary"] == answer["summary"]
    compact_size = len(json.dumps(compact, ensure_ascii=False).encode())
    original_size = len(json.dumps(answer, ensure_ascii=False).encode())
    # The complete final Markdown intentionally remains in summary.text. The
    # compact projection removes repeated evidence/tool bodies and duplicate
    # trace prose while preserving that user-visible answer.
    assert compact_size < original_size * 0.2


def test_full_answer_projection_remains_the_default(monkeypatch):
    monkeypatch.setattr(chinese, "translation_cache", lambda: {})
    monkeypatch.setattr(chinese, "present_record", lambda record, cache: {**record, "presented": True})
    answer = _answer()
    full = chinese.present_answer(answer)

    assert full["citations"][0]["text"] == answer["citations"][0]["text"]
    assert full["citations"][0]["presented"] is True
    assert full["internal_sources"][0]["ai_content"] == answer["internal_sources"][0]["ai_content"]
    assert full["question_plan"] == answer["question_plan"]
    assert "response_projection" not in full


def test_reference_only_persisted_summary_is_repaired_for_display(monkeypatch):
    monkeypatch.setattr(chinese, "translation_cache", lambda: {})
    answer = _answer()
    answer["topic"] = "ukraine"
    answer["summary"] = {"text": "（见上）", "evidence_ids": []}
    projected = chinese.present_answer(answer, compact=True)
    assert projected["summary"]["text"] != "（见上）"
    assert "结论摘要" in projected["summary"]["text"]
    assert "伊朗局势最新进展" in projected["summary"]["text"]
