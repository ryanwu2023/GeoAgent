from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from backend.internal_messages import (
    CHINA_TZ,
    get_message,
    message_stats,
    next_scheduled_fetch,
    normalize_message,
    search_messages,
    upsert_messages,
    internal_query_terms,
    message_topic_lane,
)
from scripts.ht_macro_service import MessageApi, Settings, _page_data


def fixture_message(**overrides):
    item = {
        "title": "美伊局势与俄乌谈判每日跟踪",
        "contentAbstract": "霍尔木兹通行和布伦特油价受到关注。",
        "msgId": 2683648938485114610,
        "msgType": "WX",
        "time": 1792629453000,
        "textType": "日常跟踪",
        "textClass": "国际海外",
        "tag": "无人机",
        "tags": [{"tagName": "无人机", "tagType": 2, "classification": "制造"}],
        "unqMsgId": "WX:2683648938485114610",
        "aiShort": "中东风险与俄乌谈判并存。",
        "updateTime": "2026-10-22 08:39:24",
        "extraInfo": json.dumps(
            {
                "groupId": "private-group-id",
                "groupname": "华泰政策交流群",
                "senderId": "private-sender-id",
                "sendername": "张三",
                "receiverId": "private-receiver-id",
                "receiveTime": "2026-10-22 08:37:34",
                "msgOriginType": "Text",
            },
            ensure_ascii=False,
        ),
    }
    item.update(overrides)
    return item


def test_normalize_and_deduplicate_updates_existing_record(tmp_path):
    path = tmp_path / "messages.sqlite3"
    first = upsert_messages([fixture_message()], path=path, seen_at="2026-10-22T01:00:00Z")
    duplicate = upsert_messages([fixture_message()], path=path, seen_at="2026-10-22T02:00:00Z")
    changed = upsert_messages(
        [fixture_message(aiShort="新的摘要")], path=path, seen_at="2026-10-22T03:00:00Z"
    )
    assert first == {"received": 1, "inserted": 1, "updated": 0, "duplicates": 0}
    assert duplicate == {"received": 1, "inserted": 0, "updated": 0, "duplicates": 1}
    assert changed == {"received": 1, "inserted": 0, "updated": 1, "duplicates": 0}
    found = search_messages(query="霍尔木兹", path=path)
    assert found["total"] == 1
    public = found["messages"][0]
    assert public["ai_short"] == "新的摘要"
    assert public["detail_included"] is False
    assert public["detail_tool"] == "geo_internal_message_get"
    assert "ai_content" not in public
    assert public["topics"] == ["usiran", "ukraine", "asset_macro"]
    assert public["group_name"] == "华泰政策交流群"
    assert "private-group-id" not in json.dumps(public, ensure_ascii=False)
    assert "private-sender-id" not in json.dumps(public, ensure_ascii=False)
    detail = get_message(public["id"], path=path)
    assert detail["first_seen_at"] == "2026-10-22T01:00:00Z"
    assert detail["detail_included"] is True
    assert "ai_content" in detail


def test_search_returns_bounded_preview_while_detail_keeps_full_text(tmp_path):
    path = tmp_path / "messages.sqlite3"
    long_text = "伊朗" * 2000
    upsert_messages([fixture_message(contentAbstract=long_text, aiContent=long_text)], path=path)
    preview = search_messages(query="伊朗", path=path)["messages"][0]
    assert len(preview["content_abstract"]) == 500
    assert preview["preview_truncated"] is True
    assert "ai_content" not in preview
    detail = get_message(preview["id"], path=path)
    assert detail["content_abstract"] == long_text
    assert detail["ai_content"] == long_text


def test_fingerprint_fallback_and_invalid_extra_info():
    item = fixture_message(unqMsgId=None, msgId=None, extraInfo="not-json")
    normalized = normalize_message(item)
    assert normalized["dedupe_key"].startswith("fingerprint:")
    assert normalized["extra_info_error"] == "extraInfo 不是有效 JSON"


def test_search_filters_stats_and_date_validation(tmp_path):
    path = tmp_path / "messages.sqlite3"
    upsert_messages([fixture_message()], path=path)
    result = search_messages(
        start="2026-10-22", end="2026-10-22", group_name="华泰", tag="无人机",
        text_class="国际", path=path,
    )
    assert result["total"] == 1
    stats = message_stats(path=path)
    assert stats["records"] == 1
    assert stats["relevant_records"] == 1
    assert stats["tags"] == [{"name": "无人机", "count": 1}]


def test_message_api_uses_exact_login_refresh_and_list_contract():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append((request.url.path, request.headers.get("authorization"), payload))
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"code": "000000", "status": True, "data": {"access_token": "token-1"}})
        if request.url.path.endswith("/refresh/token"):
            return httpx.Response(200, json={"code": "000000", "status": True, "data": {"accessToken": "token-2"}})
        return httpx.Response(200, json={"code": "000000", "status": True, "data": {"total": 1, "list": [fixture_message()]}})

    settings = Settings(
        base_url="http://internal.example", region="+86", username="user", password="secret",
        client_category="1", enabled=True,
    )
    api = MessageApi(settings, transport=httpx.MockTransport(handler))
    try:
        api.login()
        api.refresh()
        total, rows = _page_data(api.fetch_page(1))
    finally:
        api.close()
    assert total == 1 and len(rows) == 1
    assert calls[0] == (
        "/application/auth/oauth/two-stage/login", None,
        {"region": "+86", "username": "user", "password": "secret", "clientCategory": "1"},
    )
    assert calls[1] == (
        "/application/auth/oauth/refresh/token", "Bearer token-1", {"accessToken": "token-1"}
    )
    assert calls[2][0] == "/application/lgrp-server/lgrp/api/msg/list"
    assert calls[2][1] == "Bearer token-2"
    assert calls[2][2] == {
        "pageNum": 1, "pageSize": 20, "msgTypes": ["WX"], "infoFlowSource": "external",
        "isImportant": None, "isShowMyCncpt": 0, "queryMsgType": 3,
    }


def test_next_fetch_uses_china_fixed_schedule():
    times = tuple(datetime.strptime(value, "%H:%M").time() for value in ("08:00", "14:00", "20:00"))
    now = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)  # 09:00 Beijing
    scheduled = next_scheduled_fetch(now, times)
    assert scheduled.astimezone(CHINA_TZ).isoformat().startswith("2026-10-22T14:00:00")


def test_internal_query_terms_split_entities_events_and_assets():
    terms = internal_query_terms("最新美伊谈判与原油影响")
    assert terms[:2] == ["伊朗", "美国"]
    assert "谈判" in terms
    assert "原油" in terms


def test_message_topic_lane_uses_real_text_not_generic_topic_bridge():
    direct = {
        "title": "伊朗与美国恢复间接谈判",
        "content_abstract": "双方讨论制裁与霍尔木兹通行。",
        "topics": ["usiran"],
        "relevance_status": "relevant",
    }
    market_context = {
        "title": "中东风险推动原油价格上涨",
        "content_abstract": "市场关注运输成本和风险偏好。",
        "topics": ["asset_macro"],
        "relevance_status": "relevant",
    }
    polluted = {
        "title": "俄罗斯提出协助斡旋伊朗问题",
        "content_abstract": "莫斯科与德黑兰讨论中东局势。",
        "topics": ["ukraine", "usiran"],
        "relevance_status": "relevant",
    }
    assert message_topic_lane("美伊最新进展", direct) == "core"
    assert message_topic_lane("美伊最新进展", market_context) == "cross_topic"
    assert message_topic_lane("俄乌最新进展", polluted) == "cross_topic"
    assert message_topic_lane("俄乌最新进展", {**polluted, "relevance_status": "irrelevant"}) == "rejected"
