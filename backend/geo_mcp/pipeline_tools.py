"""采集管线 MCP 工具：只读查看管线、版本、运行与新增条目。

所有持有有效 GeoAgent MCP Key 的调用方都可调用，操作者记为 mcp:<principal_id>。
返回值是投影：不含源站正文与 raw_files，列表与分页有上限，摘要截断 500 字。
写入类工具（预览、新建、更新、测试运行、启停、删除、回滚）同样不区分管理员，只校验工具白名单。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from pydantic import Field

from backend import pipeline_runs as _runs_module, pipeline_service
from backend.pipeline_config import PipelineConfigError, default_resolve, validate_pipeline, validate_row
from backend.pipeline_scheduler import pipelines_enabled
from backend.pipeline_service import Actor, PipelineError
from backend.store import db
from .contracts import MCPError
from .scope import _allows, authorize

log = logging.getLogger(__name__)

MAX_LIST = 50
MAX_PREVIEW = 20
MAX_ITEMS = 50
SUMMARY_LIMIT = 500
MAX_HOURS = 168
CONFIG_SHAPE = (
    ' row 形如 {"name":"必填","description":"可空","topics":["usiran","ukraine"]}（topics 至少 1 个，只能取这两个值）。'
    'config 形如 {"sources":[来源...],"filters":{...},"request":{...}}，sources 1 到 30 个，每个来源必填 id'
    '(小写字母数字连字符，≤64位，不重复)、type(web 或 rss)、name、allowed_hosts(1到10个小写主机名，不含协议路径端口)、'
    'tier(T1/T2/T3)、content_policy(public_fulltext 或 metadata_only)；rss 来源另需 feed_url 且不得带 entry_urls，'
    'web 来源另需 entry_urls(1到10个链接)且不得带 feed_url；可选 language(默认 en)、link_include_pattern(正则，≤200字)。'
    '链接的主机必须在 allowed_hosts 内；allowed_hosts 必须包含文章链接所在的全部域名，而不只是订阅源自身的域名（如订阅源在 feeds.bbci.co.uk、文章链接在 www.bbc.co.uk 时两者都要写），否则条目被丢弃，来源报 LINKS_OUTSIDE_ALLOWED_HOSTS 并在 error_message 列出被丢弃的域名。filters 与 request 可省略，默认值：filters={"include_any":[],"include_strong":[],'
    '"exclude":[],"require_category":true,"lookback_days":14(1到90),"max_candidates_per_source":80(1到200)}，'
    'request={"timeout_seconds":25(1到60),"rate_limit_seconds":1.5(0到30)}。')
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
    from backend.remote_collection import collection_mode  # 惰性导入：remote_collection 会反向导入 geo_mcp
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
    if isinstance(value, bool) or not isinstance(value, int):
        raise MCPError("GEO_INVALID_ARGUMENT", f"{field} 必须是整数", {"field": field})
    return max(1, min(value, maximum))


def _int(value, field: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MCPError("GEO_INVALID_ARGUMENT", f"{field} 必须是不小于 {minimum} 的整数", {"field": field})
    return value


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


def _project(row: dict) -> dict:
    """管线行的 JSON 安全投影（时间转 ISO 字符串）；读、写工具共用。"""
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"], "description": row["description"],
        "topics": list(row["topics"]), "enabled": row["enabled"], "current_version": row["current_version"],
        "last_run_id": row["last_run_id"], "last_status": row["last_status"], "last_trigger": row["last_trigger"],
        "last_finished_at": _iso(row["last_finished_at"]), "last_new_items": row["last_new_items"],
        "created_by": row["created_by"], "created_at": _iso(row["created_at"]),
        "updated_by": row["updated_by"], "updated_at": _iso(row["updated_at"]),
        "config": row.get("config"),
    }


def pipeline_get(principal, pipeline_id: str) -> dict:
    _guard(write=False)
    return _project(_read(pipeline_service.get_pipeline, pipeline_id))


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
                     "candidates": s.get("candidates", 0), "accepted": s.get("accepted", 0),
                     **({"error_message": s["error_message"]} if s.get("error_message") else {})}
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
    start = _int(offset, "offset", 0)
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
            "WHERE NOT r.is_deleted AND r.trigger='batch' AND r.finished_at IS NOT NULL AND r.finished_at >= now() - make_interval(hours => %s) "
            "GROUP BY p.id, p.name ORDER BY new_items DESC, p.name", (hours,)).fetchall()
    return [{"pipeline_id": r["pipeline_id"], "name": r["name"], "runs": r["runs"], "new_items": int(r["new_items"]),
             "last_trigger": r["last_trigger"], "last_finished_at": _iso(r["last_finished_at"])} for r in rows]


def _object(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise MCPError("GEO_INVALID_ARGUMENT", f"{field} 必须是对象", {"field": field})
    return value


def _write(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except PipelineError as exc:
        _raise(exc)


def pipeline_preview(principal, row: dict, config: dict, limit: int = 5, *, resolve=default_resolve) -> dict:
    _guard(write=True)
    _object(row, "row")
    _object(config, "config")
    page = _clamp(limit, MAX_PREVIEW)
    return _write(_runs_module.preview_pipeline, row, config, limit=page, resolve=resolve)


def pipeline_create(principal, row: dict, config: dict, change_note: str = "", dry_run: bool = False, *,
                    resolve=default_resolve) -> dict:
    _guard(write=True)
    _object(row, "row")
    _object(config, "config")
    if dry_run:
        try:
            clean_row, clean_config = validate_pipeline(row, config, resolve=resolve)
        except PipelineConfigError as exc:
            raise MCPError("GEO_INVALID_ARGUMENT", exc.message, {"field": exc.field}) from exc
        return {"dry_run": True, "row": clean_row, "config": clean_config}
    return _project(_write(pipeline_service.create_pipeline, _actor(principal), row, config, change_note or "",
                           resolve=resolve))


def _update_changes(current: dict, expected_version: int, row_patch: dict, config: dict | None, resolve) -> dict:
    if current["kind"] == "builtin":
        raise PipelineError("BUILTIN_READONLY", "内置管线只允许修改启停", {"fields": sorted(row_patch)})
    if current["current_version"] != expected_version:
        raise PipelineError("VERSION_CONFLICT", "管线已被他人修改，请刷新后重试",
                            {"current_version": current["current_version"]})
    unknown = set(row_patch) - set(pipeline_service.ROW_FIELDS)
    if unknown:
        raise PipelineError("INVALID", f"不支持修改的字段：{'、'.join(sorted(unknown))}", {"field": sorted(unknown)[0]})
    merged = {k: current[k] for k in pipeline_service.ROW_FIELDS}
    merged.update(row_patch)
    try:
        if config is not None:
            clean_row, clean_config = validate_pipeline(merged, config, resolve=resolve)
        else:
            clean_row, clean_config = validate_row(merged), None
    except PipelineConfigError as exc:
        raise PipelineError("INVALID", exc.message, {"field": exc.field}) from exc
    changes = {k: [current[k], clean_row[k]] for k in pipeline_service.ROW_FIELDS if current[k] != clean_row[k]}
    if clean_config is not None and clean_config != current.get("config"):
        changes["config"] = [current.get("config"), clean_config]
    return changes


def pipeline_update(principal, pipeline_id: str, expected_version: int, row_patch: dict | None = None,
                    config: dict | None = None, change_note: str = "", dry_run: bool = False, *,
                    resolve=default_resolve) -> dict:
    _guard(write=True)
    version = _int(expected_version, "expected_version", 1)
    patch = {} if row_patch is None else _object(row_patch, "row_patch")
    if config is not None:
        _object(config, "config")
    if dry_run:
        current = _write(pipeline_service.get_pipeline, pipeline_id)
        changes = _write(_update_changes, current, version, patch, config, resolve)
        return {"dry_run": True, "current_version": current["current_version"], "changes": changes}
    return _project(_write(pipeline_service.update_pipeline, _actor(principal), pipeline_id, expected_version=version,
                           row_patch=patch, config=config, change_note=change_note or "", resolve=resolve))


def _background_trial(pipeline_id: str, username: str) -> None:
    try:
        _runs_module.run_trial(pipeline_id, actor=username)
    except PipelineError as exc:
        log.warning("管线 %s 测试运行未执行：%s", pipeline_id, exc.message)
    except Exception:  # noqa: BLE001 - 后台线程不能让异常外泄
        log.exception("管线 %s 测试运行失败", pipeline_id)


def pipeline_trial(principal, pipeline_id: str) -> dict:
    _guard(write=True)
    pipeline = _write(pipeline_service.get_pipeline, pipeline_id)
    if pipeline["kind"] != "configurable":
        raise MCPError("GEO_INVALID_ARGUMENT", _runs_module.BUILTIN_NO_TRIAL, {"id": pipeline_id})
    if _runs_module.trial_running(pipeline_id):
        raise MCPError("GEO_CONFLICT", _runs_module.TRIAL_CONFLICT, {"id": pipeline_id})
    threading.Thread(target=_background_trial, args=(pipeline_id, _actor(principal).username), daemon=True,
                     name=f"pipeline-trial-{pipeline_id}").start()
    return {"pipeline_id": pipeline_id, "accepted": True, "hint": "通常需要数分钟，可多次调用 geo_pipeline_runs 查看；最长 15 分钟"}


def pipeline_set_enabled(principal, pipeline_id: str, enabled: bool) -> dict:
    _guard(write=True)
    if not isinstance(enabled, bool):
        raise MCPError("GEO_INVALID_ARGUMENT", "enabled 必须是布尔值", {"field": "enabled"})
    return _project(_write(pipeline_service.set_enabled, _actor(principal), pipeline_id, enabled))


def pipeline_delete(principal, pipeline_id: str) -> dict:
    _guard(write=True)
    _write(pipeline_service.delete_pipeline, _actor(principal), pipeline_id)
    return {"deleted": True}


def pipeline_rollback(principal, pipeline_id: str, to_version: int, expected_version: int, *,
                      resolve=default_resolve) -> dict:
    _guard(write=True)
    return _project(_write(pipeline_service.rollback, _actor(principal), pipeline_id,
                           to_version=_int(to_version, "to_version", 1),
                           expected_version=_int(expected_version, "expected_version", 1), resolve=resolve))


PipelineId = Annotated[str, Field(max_length=120, description="管线ID，从geo_pipeline_list获取。")]

Row = Annotated[dict, Field(description="管线基本信息：name、description、topics(主题机器值数组)。")]
Config = Annotated[dict, Field(description="管线配置：sources(来源数组，1到30个)、filters、request；见工具说明。")]
Note = Annotated[str, Field(max_length=500, description="本次修改说明，写入版本历史，可为空。")]


def register_pipeline_tools(server, run_tool=None) -> None:
    if run_tool is None:
        from .server import _run_tool as run_tool  # 惰性导入，避免与 server.py 循环依赖
    read_only = {"readOnlyHint": True, "destructiveHint": False}

    def tool(name: str, description: str):
        return server.tool(name=name, description=description, annotations=read_only, structured_output=False)

    def write_tool(name: str, description: str, destructive: bool = False):
        return server.tool(name=name, description=description, structured_output=False,
                           annotations={"readOnlyHint": False, "destructiveHint": destructive})

    async def run(ctx: Context, name: str, call, write: bool = False):
        def handler(principal, _):
            if write:  # 写工具不走 authorize（它要求 read_only），只校验工具白名单
                if not _allows(principal.allowed_tools, name):
                    raise MCPError("GEO_TOOL_NOT_ALLOWED", f"工具未授权: {name}", {"tool": name})
            else:
                authorize(principal, tool=name)
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

    @tool("geo_pipeline_recent_results", "汇总最近若干小时内各管线整批采集（batch，进快照）的运行情况，不含测试运行：运行次数、新增条数（按新增降序）、最近一次触发方式和完成时间。用于回答“最近采到了什么”，再用 geo_pipeline_run_items 看明细。")
    async def geo_pipeline_recent_results(
        ctx: Context,
        hours: Annotated[int, Field(description="回看小时数，1到168，默认24。")] = 24,
    ) -> str:
        return await run(ctx, "geo_pipeline_recent_results", lambda p: pipeline_recent_results(p, hours))

    @write_tool("geo_pipeline_preview", "试跑：按给定配置对每个来源抓取前几个候选并显示过滤结果，不写库、不落文件、不进快照。用于新建或修改前确认来源能抓到内容、过滤词合理；耗时可能达数十秒。" + CONFIG_SHAPE)
    async def geo_pipeline_preview(
        ctx: Context, row: Row, config: Config,
        limit: Annotated[int, Field(description="每个来源最多展示的候选数，1到20，默认5。")] = 5,
    ) -> str:
        return await run(ctx, "geo_pipeline_preview", lambda p: pipeline_preview(p, row, config, limit), write=True)

    @write_tool("geo_pipeline_create", "新建采集管线。新管线默认停用，不参与整批采集；推荐流程：新建→geo_pipeline_preview 试跑→geo_pipeline_trial 测试运行→geo_pipeline_runs/geo_pipeline_run_items 展示结果→用户明确同意→geo_pipeline_set_enabled 启用。dry_run=true 只校验并返回规范化后的 row/config，不写库，也不检查同名管线是否已存在。" + CONFIG_SHAPE)
    async def geo_pipeline_create(
        ctx: Context, row: Row, config: Config, change_note: Note = "",
        dry_run: Annotated[bool, Field(description="true 时只校验并返回规范化结果，不创建。")] = False,
    ) -> str:
        return await run(ctx, "geo_pipeline_create", lambda p: pipeline_create(p, row, config, change_note, dry_run), write=True)

    @write_tool("geo_pipeline_update", "修改管线的名称/描述/主题（row_patch）和配置（config，整份替换，结构见 geo_pipeline_create）。必须带 expected_version（来自 geo_pipeline_get 的 current_version），版本不一致返回 GEO_VERSION_CONFLICT 并给出最新版本；配置变化会生成新版本。内置管线不可改。dry_run=true 只返回 {current_version, changes}，不写库。")
    async def geo_pipeline_update(
        ctx: Context, pipeline_id: PipelineId,
        expected_version: Annotated[int, Field(description="调用方读到的当前版本号，防止覆盖他人修改。")],
        row_patch: Annotated[dict, Field(description="要修改的基本信息字段，仅 name/description/topics，可为空对象。")] = {},
        config: Annotated[dict | None, Field(description="新的完整配置；不改配置时省略。")] = None,
        change_note: Note = "",
        dry_run: Annotated[bool, Field(description="true 时只返回将发生的变化，不写库。")] = False,
    ) -> str:
        return await run(ctx, "geo_pipeline_update",
                         lambda p: pipeline_update(p, pipeline_id, expected_version, row_patch, config, change_note, dry_run), write=True)

    @write_tool("geo_pipeline_trial", "对管线发起一次测试运行：后台执行，立即返回；产物写到测试目录，不进快照、不写去重台账。通常需要数分钟（最长 15 分钟），可多次调用 geo_pipeline_runs 查看状态、geo_pipeline_run_items 查看条目。内置管线不支持；同一管线已有测试运行时返回 GEO_CONFLICT。")
    async def geo_pipeline_trial(ctx: Context, pipeline_id: PipelineId) -> str:
        return await run(ctx, "geo_pipeline_trial", lambda p: pipeline_trial(p, pipeline_id), write=True)

    @write_tool("geo_pipeline_set_enabled", "启用或停用管线。启用前先向用户展示最近一次测试运行结果并取得明确同意。启用后该管线从下一次整批采集起加入全量采集，结果进入快照；停用后其旧产物不再进入快照。内置管线也可通过此工具启停。", destructive=True)
    async def geo_pipeline_set_enabled(
        ctx: Context, pipeline_id: PipelineId,
        enabled: Annotated[bool, Field(description="true 启用，false 停用。")],
    ) -> str:
        return await run(ctx, "geo_pipeline_set_enabled", lambda p: pipeline_set_enabled(p, pipeline_id, enabled), write=True)

    @write_tool("geo_pipeline_delete", "删除自定义管线（软删除，同时停用并将其产物移出快照导入范围）。内置管线不可删除。删除前应先向用户确认。", destructive=True)
    async def geo_pipeline_delete(ctx: Context, pipeline_id: PipelineId) -> str:
        return await run(ctx, "geo_pipeline_delete", lambda p: pipeline_delete(p, pipeline_id), write=True)

    @write_tool("geo_pipeline_rollback", "把管线配置回滚到历史版本：以该版本配置生成一个新版本（版本号递增，历史不丢）。需 expected_version（当前版本）防并发覆盖，to_version 不能等于当前版本。")
    async def geo_pipeline_rollback(
        ctx: Context, pipeline_id: PipelineId,
        to_version: Annotated[int, Field(description="要回滚到的历史版本号，从 geo_pipeline_versions 获取。")],
        expected_version: Annotated[int, Field(description="调用方读到的当前版本号。")],
    ) -> str:
        return await run(ctx, "geo_pipeline_rollback", lambda p: pipeline_rollback(p, pipeline_id, to_version, expected_version), write=True)
