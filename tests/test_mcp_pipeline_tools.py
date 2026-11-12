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
    reads = {"geo_pipeline_list", "geo_pipeline_get", "geo_pipeline_versions", "geo_pipeline_runs",
             "geo_pipeline_run_items", "geo_pipeline_recent_results"}
    writes = {"geo_pipeline_preview", "geo_pipeline_create", "geo_pipeline_update", "geo_pipeline_trial",
              "geo_pipeline_set_enabled", "geo_pipeline_delete", "geo_pipeline_rollback"}
    assert set(found) == reads | writes
    for name, tool in found.items():
        if name in reads:
            assert tool.annotations.readOnlyHint is True and tool.annotations.destructiveHint is False
        else:
            assert tool.annotations.readOnlyHint is False
    assert found["geo_pipeline_delete"].annotations.destructiveHint is True
    assert found["geo_pipeline_set_enabled"].annotations.destructiveHint is True
    assert "启用前先向用户展示最近一次测试运行结果并取得明确同意" in found["geo_pipeline_set_enabled"].description
    assert "默认停用" in found["geo_pipeline_create"].description
    for tool in found.values():
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


ROW = {"name": "写入管线", "description": "d", "topics": ["usiran"]}


def test_create_then_update_with_version(principal):
    p = tools.pipeline_create(principal, ROW, cfg(), "初版", resolve=resolve)
    assert p["enabled"] is False and p["current_version"] == 1 and p["created_by"] == "mcp:hft-key"
    new = cfg()
    new["filters"]["include_any"].append("gas")
    up = tools.pipeline_update(principal, p["id"], 1, {"description": "新描述"}, new, "加词", resolve=resolve)
    assert up["current_version"] == 2 and up["description"] == "新描述"
    with pytest.raises(MCPError) as err:
        tools.pipeline_update(principal, p["id"], 1, {"description": "x"}, None, resolve=resolve)
    assert err.value.code == "GEO_VERSION_CONFLICT" and err.value.details["current_version"] == 2
    rolled = tools.pipeline_rollback(principal, p["id"], 1, 2, resolve=resolve)
    assert rolled["current_version"] == 3
    assert tools.pipeline_set_enabled(principal, p["id"], True)["enabled"] is True
    assert tools.pipeline_set_enabled(principal, p["id"], False)["enabled"] is False
    assert tools.pipeline_delete(principal, p["id"]) == {"deleted": True}
    with pytest.raises(MCPError) as err:
        tools.pipeline_get(principal, p["id"])
    assert err.value.code == "GEO_NOT_FOUND"


def test_update_requires_expected_version(principal):
    p = tools.pipeline_create(principal, {**ROW, "name": "需版本"}, cfg(), resolve=resolve)
    for bad in (None, "1", True, 0):
        with pytest.raises(MCPError) as err:
            tools.pipeline_update(principal, p["id"], bad, {"description": "x"}, None, resolve=resolve)
        assert err.value.code == "GEO_INVALID_ARGUMENT" and err.value.details["field"] == "expected_version"


def test_update_dry_run_no_write(principal):
    p = tools.pipeline_create(principal, {**ROW, "name": "干跑"}, cfg(), resolve=resolve)
    new = cfg()
    new["filters"]["include_any"] = ["gas"]
    out = tools.pipeline_update(principal, p["id"], 1, {"description": "改"}, new, dry_run=True, resolve=resolve)
    assert out["current_version"] == 1
    assert out["changes"]["description"] == ["d", "改"]
    assert out["changes"]["config"][1]["filters"]["include_any"] == ["gas"]
    after = tools.pipeline_get(principal, p["id"])
    assert after["current_version"] == 1 and after["description"] == "d"
    assert len(tools.pipeline_versions(principal, p["id"])) == 1
    with pytest.raises(MCPError) as err:  # 干跑仍校验
        tools.pipeline_update(principal, p["id"], 1, {"name": ""}, None, dry_run=True, resolve=resolve)
    assert err.value.code == "GEO_INVALID_ARGUMENT"
    with pytest.raises(MCPError) as err:
        tools.pipeline_update(principal, p["id"], 9, {}, None, dry_run=True, resolve=resolve)
    assert err.value.code == "GEO_VERSION_CONFLICT"
    dry = tools.pipeline_create(principal, {**ROW, "name": "只校验"}, cfg(), dry_run=True, resolve=resolve)
    assert dry["row"]["name"] == "只校验" and dry["config"]["sources"][0]["id"] == "rss-a"
    assert tools.pipeline_list(principal, q="只校验")["total"] == 0


def test_create_rejects_bad_types(principal):
    for row, config in (("x", cfg()), (ROW, "x"), (ROW, {"sources": []}), ({**ROW, "topics": "a"}, cfg())):
        with pytest.raises(MCPError) as err:
            tools.pipeline_create(principal, row, config, resolve=resolve)
        assert err.value.code == "GEO_INVALID_ARGUMENT"
    with pytest.raises(MCPError) as err:
        tools.pipeline_preview(principal, ROW, cfg(), limit="x", resolve=resolve)
    assert err.value.code == "GEO_INVALID_ARGUMENT"


def test_preview_passes_limit(principal, monkeypatch):
    seen = {}

    def fake(row, config, *, limit, **kw):
        seen.update(row=row, limit=limit)
        return {"sources": []}
    monkeypatch.setattr(tools._runs_module, "preview_pipeline", fake)
    assert tools.pipeline_preview(principal, ROW, cfg(), limit=99) == {"sources": []}
    assert seen["limit"] == 20


def test_trial_returns_immediately(principal, monkeypatch):
    import threading
    p = tools.pipeline_create(principal, {**ROW, "name": "试跑"}, cfg(), resolve=resolve)
    started, release = threading.Event(), threading.Event()

    def fake_trial(pid, *, actor):
        assert actor == "mcp:hft-key"
        started.set()
        release.wait(5)
    monkeypatch.setattr(tools._runs_module, "run_trial", fake_trial)
    out = tools.pipeline_trial(principal, p["id"])
    assert out["accepted"] is True and out["pipeline_id"] == p["id"] and "geo_pipeline_runs" in out["hint"]
    assert started.wait(5)
    release.set()
    with pytest.raises(MCPError) as err:
        tools.pipeline_trial(principal, "不存在")
    assert err.value.code == "GEO_NOT_FOUND"
    monkeypatch.setattr(tools._runs_module, "trial_running", lambda pid: True)
    with pytest.raises(MCPError) as err:
        tools.pipeline_trial(principal, p["id"])
    assert err.value.code == "GEO_CONFLICT"


def add_builtin():
    with db() as conn:
        conn.execute("INSERT INTO collection_pipelines(id,kind,name,description,topics,enabled,current_version,owner,created_by) "
                     "VALUES ('b-1','builtin','内置','',ARRAY['usiran'],true,1,'system','system')")
    return "b-1"


def test_builtin_write_rejected(principal):
    bid = add_builtin()
    with pytest.raises(MCPError) as err:
        tools.pipeline_update(principal, bid, 1, {"description": "x"}, None, resolve=resolve)
    assert err.value.code == "GEO_INVALID_ARGUMENT"
    with pytest.raises(MCPError) as err:
        tools.pipeline_update(principal, bid, 1, {"description": "x"}, None, dry_run=True, resolve=resolve)
    assert err.value.code == "GEO_INVALID_ARGUMENT"
    for call in (lambda: tools.pipeline_delete(principal, bid), lambda: tools.pipeline_trial(principal, bid),
                 lambda: tools.pipeline_rollback(principal, bid, 1, 1)):
        with pytest.raises(MCPError) as err:
            call()
        assert err.value.code == "GEO_INVALID_ARGUMENT"


def test_remote_mode_blocks_writes(principal, monkeypatch):
    p = tools.pipeline_create(principal, {**ROW, "name": "远端"}, cfg(), resolve=resolve)
    monkeypatch.setenv("GEO_COLLECTION_MODE", "remote")
    calls = (lambda: tools.pipeline_preview(principal, ROW, cfg()),
             lambda: tools.pipeline_create(principal, ROW, cfg()),
             lambda: tools.pipeline_update(principal, p["id"], 1, {}, None),
             lambda: tools.pipeline_trial(principal, p["id"]),
             lambda: tools.pipeline_set_enabled(principal, p["id"], True),
             lambda: tools.pipeline_delete(principal, p["id"]),
             lambda: tools.pipeline_rollback(principal, p["id"], 1, 1))
    for call in calls:
        with pytest.raises(MCPError) as err:
            call()
        assert err.value.code == "GEO_REMOTE_READ_ONLY"
    assert tools.pipeline_get(principal, p["id"])["id"] == p["id"]


def test_versions_record_mcp_channel(principal):
    p = tools.pipeline_create(principal, {**ROW, "name": "渠道"}, cfg(), "初版", resolve=resolve)
    new = cfg()
    new["filters"]["exclude"] = ["x"]
    tools.pipeline_update(principal, p["id"], 1, {}, new, "改", resolve=resolve)
    versions = tools.pipeline_versions(principal, p["id"])
    assert [v["source_channel"] for v in versions] == ["mcp", "mcp"]
    assert versions[0]["created_by"] == "mcp:hft-key"


def test_write_tools_check_whitelist_only(principal):
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

    def call(allowed, read_only):
        scope = frozenset({"*"})
        seen["principal"] = Principal("k", "a", "p", scope, scope, scope, frozenset(allowed), read_only)
        result = asyncio.run(server.call_tool("geo_pipeline_delete", {"pipeline_id": "不存在"}))
        return result[0][0].text if isinstance(result, tuple) else result[0].text

    assert "GEO_TOOL_NOT_ALLOWED" in call({"geo_pipeline_list"}, False)
    assert "GEO_NOT_FOUND" in call({"geo_pipeline_delete"}, False)
    assert "GEO_NOT_FOUND" in call({"*"}, True)


def test_doc_example_config_is_valid():
    import re
    from pathlib import Path
    from backend.pipeline_config import validate_pipeline
    doc = (Path(__file__).resolve().parents[1] / "docs/agent/geo-pipeline-mcp.md").read_text(encoding="utf-8")
    example = json.loads(re.search(r"```json\n(.*?)\n```", doc, re.S).group(1))
    row, config = validate_pipeline(example["row"], example["config"], resolve=resolve)
    assert row["topics"] == ["usiran"] and len(config["sources"]) == 2


def test_workbench_write_tools_admin_only(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from fastapi import HTTPException
    from backend import mcp_workbench, web_auth

    def call(tool, user, enabled=True):
        monkeypatch.setattr(web_auth, "enabled", lambda: enabled)
        monkeypatch.setattr(web_auth, "users", lambda: {"root": object()})
        req = SimpleNamespace(state=SimpleNamespace(web_user=user))
        body = mcp_workbench.Trial(tool=tool, arguments={"pipeline_id": "不存在"})
        return asyncio.run(mcp_workbench.trial(body, req, SimpleNamespace(status_code=200)))

    for user in ("bob", None):
        with pytest.raises(HTTPException) as err:
            call("geo_pipeline_delete", user)
        assert err.value.status_code == 403 and "仅管理员" in err.value.detail
    assert call("geo_pipeline_delete", "root")  # 管理员放行（管线不存在，返回工具错误体）
    assert call("geo_pipeline_delete", None, enabled=False)  # 未启用网页登录视为本机管理员
    assert call("geo_pipeline_get", "bob")  # 只读工具不受限


def test_write_tools_survive_real_envelope(principal, monkeypatch):
    """走真实 _execute_tool/success_envelope：写工具返回值必须可 JSON 序列化。"""
    import asyncio
    import socket
    from mcp.server.fastmcp import FastMCP
    from backend.geo_mcp import server as srv
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    monkeypatch.setattr(srv, "_context_principal", lambda ctx: principal)
    monkeypatch.setattr(srv, "request_id_from_context", lambda ctx: "req-1")
    monkeypatch.setattr(tools._runs_module, "run_trial", lambda pid, *, actor: None)

    async def real_run_tool(*, ctx, tool, handler):
        return srv._execute_tool(ctx=ctx, tool=tool, handler=handler)

    server = FastMCP("t")
    tools.register_pipeline_tools(server, real_run_tool)

    def call(name, **args):
        result = asyncio.run(server.call_tool(name, args))
        text = result[0][0].text if isinstance(result, tuple) else result[0].text
        payload = json.loads(text)
        assert payload["ok"] is True, payload
        return payload["data"]

    created = call("geo_pipeline_create", row={**ROW, "name": "信封"}, config=cfg(), change_note="初版")
    pid = created["id"]
    assert created["enabled"] is False and isinstance(created["created_at"], str)
    new = cfg()
    new["filters"]["include_any"] = ["gas"]
    assert call("geo_pipeline_update", pipeline_id=pid, expected_version=1, config=new)["current_version"] == 2
    assert call("geo_pipeline_rollback", pipeline_id=pid, to_version=1, expected_version=2)["current_version"] == 3
    assert call("geo_pipeline_set_enabled", pipeline_id=pid, enabled=True)["enabled"] is True
    assert call("geo_pipeline_trial", pipeline_id=pid)["accepted"] is True
    assert call("geo_pipeline_get", pipeline_id=pid)["id"] == pid
    assert call("geo_pipeline_delete", pipeline_id=pid) == {"deleted": True}


def test_runs_projection_includes_error_message(principal):
    p = make("错误信息")
    stats = {"candidates": 0, "accepted": 0, "new_items": 0, "sources": {
        "rss-a": {"state": "no_data", "error": "LINKS_OUTSIDE_ALLOWED_HOSTS", "error_message": "域名不在允许列表：x（2 条）",
                  "candidates": 0, "accepted": 0}}}
    with db() as conn:
        conn.execute("INSERT INTO collection_pipeline_runs(run_id,pipeline_id,version,trigger,status,started_at,finished_at,stats,data_timestamp) "
                     "VALUES ('r-e',%s,1,'trial','success',now(),now(),%s,now())", (p["id"], json.dumps(stats)))
    source = tools.pipeline_runs(principal, p["id"])[0]["stats"]["sources"]["rss-a"]
    assert source["error"] == "LINKS_OUTSIDE_ALLOWED_HOSTS" and "x（2 条）" in source["error_message"]
