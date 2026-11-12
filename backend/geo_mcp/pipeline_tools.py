"""采集管线 MCP 工具：只读查看管线、版本、运行与新增条目。

所有持有有效 GeoAgent MCP Key 的调用方都可调用，操作者记为 mcp:<principal_id>。
返回值是投影：不含源站正文与 raw_files，列表与分页有上限，摘要截断 500 字。
写入类工具复用本模块的 _actor / _guard / _raise。
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from pydantic import Field

from backend import pipeline_runs as _runs_module, pipeline_service
from backend.pipeline_scheduler import pipelines_enabled
from backend.pipeline_service import Actor, PipelineError
from backend.remote_collection import collection_mode
from backend.store import db
from .contracts import MCPError

MAX_LIST = 50
MAX_ITEMS = 50
SUMMARY_LIMIT = 500
MAX_HOURS = 168
PROVENANCE = {"kind": "live", "source": "collection-pipelines", "live": True}

_ERROR_CODES = {
    "NOT_FOUND": "GEO_NOT_FOUND",
    "VERSION_CONFLICT": "GEO_VERSION_CONFLICT",
    "INVALID": "GEO_INVALID_ARGUMENT",
    "BUILTIN_READONLY": "GEO_INVALID_ARGUMENT",
    "CONFLICT": "GEO_CONFLICT",
    "PREVIEW_FAILED": "GEO_UPSTREAM_FAILED",
    "FORBIDDEN": "GEO_SCOPE_DENIED",
}


def _actor(principal) -> Actor:
    return Actor(f"mcp:{principal.principal_id}", True, "mcp")


def _guard(write: bool) -> None:
    if not pipelines_enabled():
        raise MCPError("GEO_FEATURE_DISABLED", "采集管线功能未启用")
    if write and collection_mode() == "remote":
        raise MCPError("GEO_REMOTE_READ_ONLY", "当前为远端采集模式，管线配置只读，请在采集端修改")


def _raise(exc: PipelineError):
    code = _ERROR_CODES.get(exc.code, "GEO_INVALID_ARGUMENT")
    raise MCPError(code, exc.message, dict(exc.details)) from exc


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _clamp(value: int, maximum: int, field: str = "limit") -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        raise MCPError("GEO_INVALID_ARGUMENT", f"{field} 必须是整数", {"field": field})


def _read(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except PipelineError as exc:
        _raise(exc)


def pipeline_list(principal, topic: str = "", status: str = "", kind: str = "", q: str = "", limit: int = MAX_LIST) -> dict:
    _guard(write=False)
    rows = _read(pipeline_service.list_pipelines, topic=topic or None, status=status or None,
                 kind=kind or None, q=q or None)
    items = [{
        "id": r["id"], "name": r["name"], "kind": r["kind"], "topics": list(r["topics"]), "enabled": r["enabled"],
        "source_count": r.get("source_count", 0), "current_version": r["current_version"],
        "last_status": r["last_status"], "last_trigger": r["last_trigger"],
        "last_finished_at": _iso(r["last_finished_at"]), "last_new_items": r["last_new_items"],
    } for r in rows[:_clamp(limit, MAX_LIST)]]
    return {"items": items, "total": len(rows)}


def pipeline_get(principal, pipeline_id: str) -> dict:
    _guard(write=False)
    row = _read(pipeline_service.get_pipeline, pipeline_id)
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"], "description": row["description"],
        "topics": list(row["topics"]), "enabled": row["enabled"], "current_version": row["current_version"],
        "last_run_id": row["last_run_id"], "last_status": row["last_status"], "last_trigger": row["last_trigger"],
        "last_finished_at": _iso(row["last_finished_at"]), "last_new_items": row["last_new_items"],
        "created_by": row["created_by"], "created_at": _iso(row["created_at"]),
        "updated_by": row["updated_by"], "updated_at": _iso(row["updated_at"]),
        "config": row.get("config"),
    }


def _term_count(config: dict) -> int:
    filters = (config or {}).get("filters") or {}
    return sum(len(filters.get(key) or []) for key in ("include_any", "include_strong", "exclude"))


def pipeline_versions(principal, pipeline_id: str, limit: int = 20) -> list[dict]:
    _guard(write=False)
    rows = _read(pipeline_service.list_versions, pipeline_id)[:_clamp(limit, MAX_LIST)]
    return [{
        "version": r["version"], "created_at": _iso(r["created_at"]), "created_by": r["created_by"],
        "change_note": r["change_note"], "source_channel": r["source_channel"],
        "source_count": len((r["config"] or {}).get("sources") or []), "term_count": _term_count(r["config"]),
    } for r in rows]


def _stats(stats: dict | None) -> dict:
    stats = stats or {}
    sources = {sid: {"state": s.get("state"), "error": s.get("error", ""),
                     "candidates": s.get("candidates", 0), "accepted": s.get("accepted", 0)}
               for sid, s in (stats.get("sources") or {}).items()}
    return {"candidates": stats.get("candidates", 0), "accepted": stats.get("accepted", 0),
            "new_items": stats.get("new_items", 0), "sources": sources}


def pipeline_runs(principal, pipeline_id: str, limit: int = 10) -> list[dict]:
    _guard(write=False)
    _read(pipeline_service.get_pipeline, pipeline_id)
    return [{
        "run_id": r["run_id"], "trigger": r["trigger"], "status": r["status"],
        "started_at": _iso(r["started_at"]), "finished_at": _iso(r["finished_at"]),
        "snapshot_id": r["snapshot_id"], "error": r["error"], "stats": _stats(r["stats"]),
    } for r in _runs_module.list_runs(pipeline_id, _clamp(limit, MAX_LIST))]


def pipeline_run_items(principal, pipeline_id: str, run_id: str = "", offset: int = 0, limit: int = 20) -> dict:
    _guard(write=False)
    _read(pipeline_service.get_pipeline, pipeline_id)
    page = _clamp(limit, MAX_ITEMS)
    start = max(0, int(offset))
    with db() as conn:
        if run_id:
            run = conn.execute("SELECT run_id, trigger FROM collection_pipeline_runs "
                               "WHERE run_id=%s AND pipeline_id=%s AND NOT is_deleted", (run_id, pipeline_id)).fetchone()
        else:
            run = conn.execute("SELECT run_id, trigger FROM collection_pipeline_runs WHERE pipeline_id=%s AND NOT is_deleted "
                               "ORDER BY started_at DESC, run_id DESC LIMIT 1", (pipeline_id,)).fetchone()
        total = 0 if run is None else conn.execute(
            "SELECT count(*) AS n FROM collection_pipeline_run_items WHERE run_id=%s AND NOT is_deleted",
            (run["run_id"],)).fetchone()["n"]
    if run is None:  # MCPError 是 frozen dataclass，不能在 db() 上下文内抛出
        if run_id:
            raise MCPError("GEO_NOT_FOUND", "运行记录不存在或不属于该管线", {"run_id": run_id})
        return {"run_id": None, "trigger": None, "items": [], "total": 0}
    rows = _runs_module.list_run_items(run["run_id"], offset=start, limit=page)
    return {"run_id": run["run_id"], "trigger": run["trigger"], "total": total, "items": [{
        "title": r["title"], "summary": (r["summary"] or "")[:SUMMARY_LIMIT], "source_id": r["source_id"],
        "source_type": r["source_type"], "published_at": _iso(r["published_at"]),
        "matched_terms": r["matched_terms"], "dimensions": r["dimensions"], "evidence_id": r["evidence_id"],
    } for r in rows]}


def pipeline_recent_results(principal, hours: int = 24) -> list[dict]:
    _guard(write=False)
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= MAX_HOURS:
        raise MCPError("GEO_INVALID_ARGUMENT", f"hours 必须是 1 到 {MAX_HOURS} 的整数", {"field": "hours"})
    with db() as conn:
        rows = conn.execute(
            "SELECT p.id AS pipeline_id, p.name, count(*) AS runs, "
            "COALESCE(sum(COALESCE((r.stats->>'new_items')::int,0)),0) AS new_items, "
            "(array_agg(r.trigger ORDER BY r.finished_at DESC))[1] AS last_trigger, max(r.finished_at) AS last_finished_at "
            "FROM collection_pipeline_runs r JOIN collection_pipelines p ON p.id=r.pipeline_id AND NOT p.is_deleted "
            "WHERE NOT r.is_deleted AND r.finished_at IS NOT NULL AND r.finished_at >= now() - make_interval(hours => %s) "
            "GROUP BY p.id, p.name ORDER BY new_items DESC, p.name", (hours,)).fetchall()
    return [{"pipeline_id": r["pipeline_id"], "name": r["name"], "runs": r["runs"], "new_items": int(r["new_items"]),
             "last_trigger": r["last_trigger"], "last_finished_at": _iso(r["last_finished_at"])} for r in rows]


PipelineId = Annotated[str, Field(max_length=120, description="管线ID，从geo_pipeline_list获取。")]


def register_pipeline_tools(server, run_tool=None) -> None:
    if run_tool is None:
        from .server import _run_tool as run_tool  # 惰性导入，避免与 server.py 循环依赖
    read_only = {"readOnlyHint": True, "destructiveHint": False}

    def tool(name: str, description: str):
        return server.tool(name=name, description=description, annotations=read_only, structured_output=False)

    async def run(ctx: Context, name: str, call):
        def handler(principal, _):
            return call(principal), PROVENANCE, []
        return await run_tool(ctx=ctx, tool=name, handler=handler)

    @tool("geo_pipeline_list", "列出采集管线（含内置与自定义），每条含启用状态、来源数、当前版本和最近一次运行结果。可按主题、状态(enabled/disabled/success/partial/failed等)、类型(configurable/builtin)、名称或描述关键词过滤；最多返回50条，total 为过滤后总数。")
    async def geo_pipeline_list(
        ctx: Context,
        topic: Annotated[str, Field(max_length=40, description="主题机器值，空串为全部。")] = "",
        status: Annotated[str, Field(max_length=20, description="状态过滤：enabled、disabled 或最近运行状态；空串为全部。")] = "",
        kind: Annotated[str, Field(max_length=20, description="管线类型：configurable 或 builtin；空串为全部。")] = "",
        q: Annotated[str, Field(max_length=100, description="名称或描述包含的关键词，空串不过滤。")] = "",
        limit: Annotated[int, Field(description="最多返回条数，1到50，默认50。")] = 50,
    ) -> str:
        return await run(ctx, "geo_pipeline_list", lambda p: pipeline_list(p, topic, status, kind, q, limit))

    @tool("geo_pipeline_get", "读取单条采集管线的详情和当前版本的完整配置（来源、过滤词、请求参数）。管线不存在或已删除时返回 GEO_NOT_FOUND。")
    async def geo_pipeline_get(ctx: Context, pipeline_id: PipelineId) -> str:
        return await run(ctx, "geo_pipeline_get", lambda p: pipeline_get(p, pipeline_id))

    @tool("geo_pipeline_versions", "列出管线的配置版本历史（新到旧），含修改说明、来源渠道(web/mcp/migration)、来源数和词数；不含配置全文，全文用 geo_pipeline_get。")
    async def geo_pipeline_versions(
        ctx: Context, pipeline_id: PipelineId,
        limit: Annotated[int, Field(description="最多返回版本数，1到50，默认20。")] = 20,
    ) -> str:
        return await run(ctx, "geo_pipeline_versions", lambda p: pipeline_versions(p, pipeline_id, limit))

    @tool("geo_pipeline_runs", "列出管线最近的运行记录（整批采集 batch 或测试运行 trial），含状态、起止时间、快照ID、错误和各来源统计。最近一次在最前。")
    async def geo_pipeline_runs(
        ctx: Context, pipeline_id: PipelineId,
        limit: Annotated[int, Field(description="最多返回运行数，1到50，默认10。")] = 10,
    ) -> str:
        return await run(ctx, "geo_pipeline_runs", lambda p: pipeline_runs(p, pipeline_id, limit))

    @tool("geo_pipeline_run_items", "分页读取某次运行新增的条目（标题、摘要最多500字、来源、命中词、证据ID），不含源站正文。run_id 为空取该管线最近一次运行；每页最多50条，total 为该运行新增总数。")
    async def geo_pipeline_run_items(
        ctx: Context, pipeline_id: PipelineId,
        run_id: Annotated[str, Field(max_length=160, description="运行ID，从geo_pipeline_runs获取；空串取最近一次运行。")] = "",
        offset: Annotated[int, Field(description="分页起点，从0开始。")] = 0,
        limit: Annotated[int, Field(description="每页条数，1到50，默认20。")] = 20,
    ) -> str:
        return await run(ctx, "geo_pipeline_run_items", lambda p: pipeline_run_items(p, pipeline_id, run_id, offset, limit))

    @tool("geo_pipeline_recent_results", "汇总最近若干小时内各管线的运行情况：运行次数、新增条数（按新增降序）、最近一次触发方式和完成时间。用于回答“最近采到了什么”，再用 geo_pipeline_run_items 看明细。")
    async def geo_pipeline_recent_results(
        ctx: Context,
        hours: Annotated[int, Field(description="回看小时数，1到168，默认24。")] = 24,
    ) -> str:
        return await run(ctx, "geo_pipeline_recent_results", lambda p: pipeline_recent_results(p, hours))
