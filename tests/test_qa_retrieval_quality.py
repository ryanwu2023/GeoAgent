import asyncio

from backend.qa_aliases import expand_query
from backend.qa_embeddings import embed_query_or_none
from backend.qa_harness_state import HarnessScope
from backend.qa_query import FacetQuery
from backend.qa_retrieval import hybrid_retrieve
from backend.qa_scope import ResearchScope


def _scope(topic="usiran"):
    from datetime import datetime, timezone
    return ResearchScope.from_question(
        "最新美伊局势" if topic == "usiran" else "最新俄乌局势",
        view_scope=HarnessScope("s1", topic), snapshot_created_at="2026-10-31T00:00:00Z",
        now=datetime(2026, 10, 31, 1, tzinfo=timezone.utc),
    )


def _record(rid, title, text, day, source, *, origin_id=""):
    return {
        "id": rid, "title": title, "text": text, "published_at": day,
        "event_at": day, "source_id": source, "source_name": source,
        "tier": "T2", "verification": "pending", "origin_id": origin_id,
    }


def test_chinese_houthi_shipping_query_recalls_english_report():
    scope = _scope()
    expanded = expand_query("胡塞袭击航运", scope)
    rows = [
        _record("hit", "Houthi attack disrupts shipping", "Ansar Allah struck a vessel in the Red Sea.", "2026-10-30", "wire"),
        _record("noise", "Sports results", "A football match ended.", "2026-10-31", "sport"),
    ]
    spec = FacetQuery.for_topic("usiran", "胡塞袭击航运", facet="military", lane="core")
    result = hybrid_retrieve(rows, "胡塞袭击航运", query_spec=spec, expanded_query=expanded)
    assert [item["id"] for item in result] == ["hit"]
    assert "keyword" in result[0]["retrieval_paths"]
    assert result[0]["rank_reason"]["score"] > 0


def test_chinese_person_name_recalls_english_original_name():
    scope = _scope()
    expanded = expand_query("佩泽希齐扬最新表态", scope)
    rows = [_record(
        "person", "Masoud Pezeshkian comments on US-Iran talks",
        "Iran's president discussed negotiations.", "2026-10-30", "official",
    )]
    result = hybrid_retrieve(rows, "佩泽希齐扬最新表态", expanded_query=expanded)
    assert result and result[0]["id"] == "person"


def test_relevance_outranks_newer_unrelated_item():
    rows = [
        _record("relevant", "霍尔木兹油轮通行下降", "霍尔木兹海峡油轮通行数据更新。", "2026-10-28", "A"),
        _record("new-noise", "伊朗国内体育新闻", "球队比赛结束。", "2026-10-31", "B"),
    ]
    result = hybrid_retrieve(rows, "最新霍尔木兹油轮通行", expanded_query=expand_query("最新霍尔木兹油轮通行", _scope()))
    assert result[0]["id"] == "relevant"


def test_same_origin_syndication_counts_once_but_denial_is_preserved():
    rows = [
        _record("copy-1", "设施遭袭", "媒体报道称设施遭袭。", "2026-10-30", "A", origin_id="wire-1"),
        _record("copy-2", "设施遭袭转载", "媒体报道称设施遭袭。", "2026-10-30", "B", origin_id="wire-1"),
        _record("denial", "设施遭袭", "官方否认设施遭袭，称报道不实。", "2026-10-30", "C"),
    ]
    result = hybrid_retrieve(rows, "设施遭袭 否认", limit=10)
    assert len([item for item in result if item["origin_group"] == "wire-1"]) == 1
    assert any(item["id"] == "denial" for item in result)


def test_semantic_provider_failure_keeps_lexical_path_available():
    class BrokenProvider:
        model_version = "broken"

        async def embed(self, texts):
            raise TimeoutError("timeout")

    assert asyncio.run(embed_query_or_none(BrokenProvider(), "霍尔木兹")) is None
    result = hybrid_retrieve([
        _record("lexical", "霍尔木兹通行", "海峡通行更新。", "2026-10-30", "A"),
    ], "霍尔木兹通行", semantic_scores={})
    assert result[0]["id"] == "lexical"
    assert result[0]["retrieval_paths"] == ["keyword"]
