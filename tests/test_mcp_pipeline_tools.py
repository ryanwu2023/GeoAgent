import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.geo_mcp import pipeline_tools as tools
from backend.geo_mcp.contracts import MCPError, Principal
from backend.pipeline_service import Actor, create_pipeline, set_enabled
from backend.store import db

ADMIN = Actor("admin", True)
NOW = datetime.now(timezone.utc)


def resolve(host):
    return ["93.184.216.34"]


def cfg():
    return {"sources": [
        {"id": "rss-a", "type": "rss", "name": "RSS 源", "feed_url": "https://example.com/feed.xml",
         "allowed_hosts": ["example.com"], "language": "en", "tier": "T2", "content_policy": "metadata_only"},
    ], "filters": {"include_any": ["iran", "oil"], "exclude": ["sport"]}}


@pytest.fixture
def principal(monkeypatch):
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "true")
    scope = frozenset({"*"})
    return Principal("hft-key", "agent", "project", scope, scope, scope, scope)


def make(name, topics=("usiran",)):
    p = create_pipeline(ADMIN, {"name": name, "description": "", "topics": list(topics)}, cfg(), resolve=resolve)
    return set_enabled(ADMIN, p["id"], True)


def add_run(pipeline_id, run_id, *, finished_ago_h, new_items, trigger="batch", status="success", items=0):
    finished = NOW - timedelta(hours=finished_ago_h)
    stats = {"candidates": 5, "accepted": 3, "new_items": new_items,
             "sources": {"rss-a": {"state": "ok", "error": "", "candidates": 5, "accepted": 3}}}
    with db() as conn:
        conn.execute(
            "INSERT INTO collection_pipeline_runs(run_id,pipeline_id,version,trigger,status,started_at,finished_at,stats,data_timestamp) "
            "VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s)",
            (run_id, pipeline_id, trigger, status, finished - timedelta(minutes=1), finished, json.dumps(stats), finished))
        for n in range(1, items + 1):
            conn.execute(
                "INSERT INTO collection_pipeline_run_items(run_id,seq,pipeline_id,record_id,evidence_id,source_id,source_type,title,"
                "summary,matched_terms,dimensions,data_timestamp) VALUES (%s,%s,%s,%s,%s,'rss-a','rss',%s,%s,'[\"iran\"]','{}',%s)",
                (run_id, n, pipeline_id, f"{run_id}-{n}", f"ev-{n}", f"标题{n}", "摘" * 800, finished))


def test_list_and_get_projection(principal):
    p = make("projection")
    add_run(p["id"], "run-1", finished_ago_h=1, new_items=2)
    listing = tools.pipeline_list(principal)
    assert listing["total"] == 1
    item = listing["items"][0]
    assert set(item) == {"id", "name", "kind", "topics", "enabled", "source_count", "current_version", "last_status",
                         "last_trigger", "last_finished_at", "last_new_items"}
    assert item["source_count"] == 1 and item["enabled"] is True
    assert tools.pipeline_list(principal, q="没有这个")["total"] == 0
    got = tools.pipeline_get(principal, p["id"])
    assert got["config"]["filters"]["include_any"] == ["iran", "oil"]
    assert "is_deleted" not in got
    versions = tools.pipeline_versions(principal, p["id"])
    assert versions[0]["version"] == 1 and versions[0]["source_count"] == 1 and versions[0]["term_count"] == 3
    assert versions[0]["source_channel"] == "web"
    runs = tools.pipeline_runs(principal, p["id"])
    assert runs[0]["run_id"] == "run-1" and runs[0]["stats"]["new_items"] == 2
    assert runs[0]["stats"]["sources"]["rss-a"]["state"] == "ok"


def test_run_items_defaults_to_latest_run(principal):
    p = make("items")
    add_run(p["id"], "run-old", finished_ago_h=5, new_items=1, items=1)
    add_run(p["id"], "run-new", finished_ago_h=1, new_items=3, items=3)
    page = tools.pipeline_run_items(principal, p["id"], limit=2)
    assert page["run_id"] == "run-new" and page["total"] == 3 and len(page["items"]) == 2
    assert len(page["items"][0]["summary"]) == 500
    assert page["items"][0]["matched_terms"] == ["iran"] and "raw_files" not in page["items"][0]
    assert tools.pipeline_run_items(principal, p["id"], run_id="run-old")["total"] == 1
    assert len(tools.pipeline_run_items(principal, p["id"], run_id="run-new", offset=2)["items"]) == 1
    with pytest.raises(MCPError) as err:
        tools.pipeline_run_items(principal, p["id"], run_id="其他管线的运行")
    assert err.value.code == "GEO_NOT_FOUND"


def test_recent_results_window(principal):
    a, b, c = make("甲"), make("乙"), make("丙")
    add_run(a["id"], "a-1", finished_ago_h=2, new_items=1)
    add_run(a["id"], "a-2", finished_ago_h=3, new_items=2, trigger="trial")
    add_run(b["id"], "b-1", finished_ago_h=1, new_items=9)
    add_run(c["id"], "c-1", finished_ago_h=30, new_items=50)
    rows = tools.pipeline_recent_results(principal, hours=24)
    assert [r["name"] for r in rows] == ["乙", "甲"]
    assert rows[1] == {"pipeline_id": a["id"], "name": "甲", "runs": 1, "new_items": 1,
                       "last_trigger": "batch", "last_finished_at": rows[1]["last_finished_at"]}
    assert len(tools.pipeline_recent_results(principal, hours=48)) == 3
    only_trial = make("仅测试")
    add_run(only_trial["id"], "t-1", finished_ago_h=1, new_items=99, trigger="trial")
    assert "仅测试" not in [r["name"] for r in tools.pipeline_recent_results(principal, hours=24)]
    assert rows[1]["runs"] == 1 and rows[1]["new_items"] == 1
    with pytest.raises(MCPError) as err:
        tools.pipeline_recent_results(principal, hours=200)
    assert err.value.code == "GEO_INVALID_ARGUMENT"


def test_feature_disabled_error(principal, monkeypatch):
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "false")
    with pytest.raises(MCPError) as err:
        tools.pipeline_list(principal)
    assert err.value.code == "GEO_FEATURE_DISABLED"


def test_not_found_maps_error(principal):
    for call in (tools.pipeline_get, tools.pipeline_versions, tools.pipeline_runs, tools.pipeline_run_items):
        with pytest.raises(MCPError) as err:
            call(principal, "不存在")
        assert err.value.code == "GEO_NOT_FOUND" and "不存在" in err.value.message
    with pytest.raises(MCPError) as err:
        tools.pipeline_list(principal, status="bogus")
    assert err.value.code == "GEO_INVALID_ARGUMENT" and err.value.details["field"] == "status"


def test_error_mapping_and_actor(principal, monkeypatch):
    from backend.pipeline_service import PipelineError
    cases = {"VERSION_CONFLICT": "GEO_VERSION_CONFLICT", "BUILTIN_READONLY": "GEO_INVALID_ARGUMENT",
             "CONFLICT": "GEO_CONFLICT", "PREVIEW_FAILED": "GEO_UPSTREAM_FAILED", "FORBIDDEN": "GEO_SCOPE_DENIED"}
    for code, mapped in cases.items():
        with pytest.raises(MCPError) as err:
            tools._raise(PipelineError(code, "中文消息", {"current_version": 3}))
        assert err.value.code == mapped and err.value.message == "中文消息"
    assert tools._actor(principal) == Actor("mcp:hft-key", True, "mcp")
    monkeypatch.setenv("GEO_COLLECTION_MODE", "remote")
    tools._guard(write=False)
    with pytest.raises(MCPError) as err:
        tools._guard(write=True)
    assert err.value.code == "GEO_REMOTE_READ_ONLY"


def test_registered_on_server():
    import asyncio
    from backend.geo_mcp.server import create_server
    listed = asyncio.run(create_server().list_tools())
    found = {t.name: t for t in listed if t.name.startswith("geo_pipeline_")}
    assert set(found) == {"geo_pipeline_list", "geo_pipeline_get", "geo_pipeline_versions", "geo_pipeline_runs",
                          "geo_pipeline_run_items", "geo_pipeline_recent_results"}
    for tool in found.values():
        assert tool.annotations.readOnlyHint is True and tool.annotations.destructiveHint is False
        assert tool.description
        for parameter in tool.inputSchema.get("properties", {}).values():
            assert parameter.get("description")


def test_invalid_paging_types(principal):
    p = make("分页")
    for kwargs in ({"offset": "x"}, {"offset": -1}, {"limit": "x"}, {"offset": 1.5}):
        with pytest.raises(MCPError) as err:
            tools.pipeline_run_items(principal, p["id"], **kwargs)
        assert err.value.code == "GEO_INVALID_ARGUMENT"


def test_tool_authorization(principal):
    import asyncio
    from mcp.server.fastmcp import FastMCP
    seen = {}

    async def fake_run_tool(*, ctx, tool, handler):
        try:
            return handler(seen["principal"], "req")[0]
        except MCPError as exc:
            return exc.code

    server = FastMCP("t")
    tools.register_pipeline_tools(server, fake_run_tool)

    def call(allowed):
        scope = frozenset({"*"})
        seen["principal"] = Principal("k", "a", "p", scope, scope, scope, frozenset(allowed))
        return asyncio.run(server.call_tool("geo_pipeline_list", {}))

    def text(result):
        return result[0][0].text if isinstance(result, tuple) else result[0].text

    assert "GEO_TOOL_NOT_ALLOWED" in text(call({"geo_snapshot_list"}))
    assert "GEO_TOOL_NOT_ALLOWED" not in text(call({"*"}))
    assert "GEO_TOOL_NOT_ALLOWED" not in text(call({"geo_pipeline_list"}))
