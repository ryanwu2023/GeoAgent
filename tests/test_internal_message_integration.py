from pathlib import Path

from backend.qa_agent import LocalGeoTools


ROOT = Path(__file__).resolve().parents[1]


def test_start_and_stop_manage_internal_message_service():
    start = (ROOT / "start.ps1").read_text(encoding="utf-8-sig")
    stop = (ROOT / "stop.ps1").read_text(encoding="utf-8-sig")
    assert "Start-InternalMessageService $python" in start
    assert "Start-InternalMessageService $existingPython" in start
    assert "ht_macro_service.py" in start
    assert "ht_macro_service.py" in stop


def test_mcp_server_exposes_internal_message_tools():
    source = (ROOT / "backend" / "geo_mcp" / "server.py").read_text(encoding="utf-8")
    for name in (
        "geo_internal_message_search", "geo_internal_message_get", "geo_internal_message_stats",
    ):
        assert f'name="{name}"' in source


def test_internal_ranking_does_not_inject_topic_words_into_every_message():
    messages = [
        {"id": "core", "title": "伊朗与美国讨论制裁", "content_abstract": "霍尔木兹通行条件", "topics": ["usiran"], "relevance_status": "relevant", "message_at": "2026-10-25"},
        {"id": "context", "title": "中东原油策略", "content_abstract": "油价与航运风险", "topics": ["asset_macro"], "relevance_status": "relevant", "message_at": "2026-10-25"},
        {"id": "noise", "title": "体育赛事", "content_abstract": "球队赢得比赛", "topics": [], "relevance_status": "unclassified", "message_at": "2026-10-25"},
    ]
    ranked = LocalGeoTools._rank_internal_messages("美伊最新进展", messages, 10)
    assert [item["id"] for item in ranked] == ["core", "context"]
    assert ranked[0]["topic_lane"] == "core"
    assert ranked[1]["topic_lane"] == "cross_topic"
