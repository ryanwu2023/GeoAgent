"""GeoAgent MCP server, usable standalone or through the project backend.

The MCP business projections remain separate from the REST API. It exposes
small, read-only projections over immutable snapshots and never exposes the
SQLite database, collector directory, or the existing REST routes.
"""

from __future__ import annotations

import json
import logging
import os
import re
from time import monotonic
from typing import Any, Callable, Annotated
from pydantic import Field

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend import research, store
from backend.geo_mcp.provenance import collection_snapshot_boundary
from backend.semantic import metric_semantics, interval_change, validate_dates, search_evidence, evidence_semantics, review_index, map_projection, collector_comparison, risk_panel_counts
from backend.link_health import link_health
from backend.chinese import localize_brief_markdown, compare_brief_markdown
from backend.semantic import project_labels
from backend.catalog import TOPICS, RULE_VERSION, topic_dimensions
from backend.events import SIGNALS, CATEGORIES, event_metadata
from backend.collector_monitor import load_ledger_events, load_runs
from backend.paths import project_root
from backend.internal_messages import get_message as get_internal_message, message_stats as internal_message_stats, search_messages as search_internal_messages
from scripts.integrate_collectors import load_registry

from .audit import record, request_id_from_context
from .auth import AuthConfig, BearerAuthMiddleware, principal_from_request
from .contracts import MCPError, MAX_TEXT_BYTES, clamp_limit, success_envelope
from .provenance import list_provenance, snapshot_provenance
from .scope import authorize


logger = logging.getLogger("geoagent.mcp")

MAX_BRIEF_BYTES = 30_000
MAX_COLLECTOR_EVENTS = 100

# Descriptions are part of the model-visible inputSchema, not only UI labels.
SnapshotId = Annotated[str, Field(description="所选看板快照ID，从geo_snapshot_list获取；不得自行替换为latest。")]
Topic = Annotated[str, Field(description="业务主题：all综合、usiran美伊局势、ukraine俄乌冲突；优先保持用户当前看板范围。", json_schema_extra={"enum": ["all", "usiran", "ukraine"]})]
Dimension = Annotated[str, Field(description="跟踪维度机器值；空串表示全部。合法值与中文名见geo_business_catalog，不按名称猜测。")]
Start = Annotated[str, Field(description="含起点YYYY-MM-DD；空串不限。证据按发布时间日期，指标按观测日期；本周基于所选快照导入日。")]
End = Annotated[str, Field(description="含终点YYYY-MM-DD；空串不限。须不早于start。不同类型时间不能冒充同一时间轴。")]
Limit = Annotated[int, Field(description="返回列表项上限1—50（采集工具1—100）；不是证据充分性或检索范围限制。")]
Offset = Annotated[int, Field(description="分页偏移，从0开始；total/has_more表示是否还有结果。")]
Series = Annotated[str, Field(description="指标series或id，从geo_metric_catalog获取；如dgs10是美债10年收益率，不能自行编造。")]
RecordId = Annotated[str, Field(description="证据ID，从检索结果或分析候选evidence_ids获取，必须属于指定快照。")]
Query = Annotated[str, Field(description="证据检索词；使用关键词、BM25、字符语义近似、实体、时间、来源等级和来源多样性混合重排序；来源等级不等于事实核验；空串按时间列出。")]
RunId = Annotated[str, Field(description="采集批次ID，从geo_collector_runs获取；空串读取最近可见批次，不代表当前进程正在运行。")]
CollectorId = Annotated[str, Field(description="采集器ID，空串为批次内全部采集器；与来源source不同。")]
InternalMessageId = Annotated[str, Field(description="内部消息公开记录ID，从geo_internal_message_search获取；不使用上游群聊或发送人内部ID。")]
InternalFilter = Annotated[str, Field(description="内部消息筛选值；空串表示不限。")]
InternalMessageLimit = Annotated[int, Field(ge=1, le=20, description="内部消息搜索每页1—20条；先搜索短摘要，再用geo_internal_message_get按ID读取少量完整内容。禁止遍历全库正文填入单次模型上下文。")]
BriefId = Annotated[str, Field(description="已归档简报ID，从geo_brief_list获取；即时问答不是档案。")]
MaxPoints = Annotated[int, Field(description="返回最近观测点上限1—50，截断标志见history_truncated；区间计算基于截断前有效点。")]
MaxBytes = Annotated[int, Field(description="简报正文UTF-8字节上限1—30000；truncated=true时不能当成完整正文。")]


def _topic_ids() -> set[str]:
    return {topic["id"] for topic in TOPICS}


def _validate_filters(topic: str, dimension: str, start: str = '', end: str = '') -> None:
    validate_dates(start, end)
    if topic != "all" and topic not in _topic_ids():
        raise MCPError("GEO_INVALID_DIMENSION", f"未知 topic: {topic}", {"topic": topic})
    if dimension:
        if topic == "all":
            valid = {dimension_id for item in TOPICS for dimension_id, _ in item["dimensions"]}
        else:
            valid = set(topic_dimensions(topic))
        if dimension not in valid:
            raise MCPError("GEO_INVALID_DIMENSION", f"未知 dimension: {dimension}", {"dimension": dimension})


def _latest_id(summaries: list[dict[str, Any]]) -> str | None:
    return summaries[0]["id"] if summaries else None


def _latest_snapshot_id() -> str | None:
    # Provenance needs IDs/dates only, not every archived record/history.
    return _latest_id(store.snapshots())


def _analysis_evidence_boundary() -> dict[str, str]:
    return {
        "verified_counterevidence_status": "not_evaluated",
        "verified_counterevidence_available": "unknown",
        "assessment_summary": "已核验反证：尚未评估，是否存在未知。不能标题写‘已核验反证——没有’；人工复核0也不能证明无反证。",
        "assessment_scope": "not_evaluated 仅表示系统未建立核验判定，不是系统已判断反证不存在或不成立。Agent 自行判断须另列依据与未核验部分，不可把此状态当成否定结论的证据。",
        "retrieval_scope": "有限检索、候选样本及旧轮次正文只支持所读材料的结论，不能据此断言整个快照没有某类数据。无完整清点时应说本轮已读材料未提供；引用旧正文须标明沿用旧回执，不能计为本轮重新核验。单方行动通报只证明该方报告了行动，不能直接写成实际行动持续或强度已核实；不同战区不能自动充当美伊反证。",
        "basis": "scenarios.counter 是假设失效条件，不是已经发生的反证。evidence_ids 是候选线索，未建立逐条条件满足关系。只证明有人表态、预测或拒绝透露细节，不能坐实停火执行、行动下降、交付中断或未见后续行动；缺乏可见证据不等于事件不存在。正文反向线索须标明推断，未独立核验不得称坐实或证实失效条件。",
    }


def _workspace_counts(snapshot: dict[str, Any], topic: str, dimension: str, start: str, end: str) -> dict[str, Any]:
    """Panel counts, distinct from the bounded scenario candidate samples."""
    records = research.filtered_records(snapshot, topic, dimension, start, end)
    with store.db() as connection:
        rows = connection.execute(
            "SELECT record_id, signal, version, created_at FROM reviews WHERE snapshot_id=? ORDER BY version",
            (snapshot["id"],),
        ).fetchall()
    latest_reviews = {row["record_id"]: row for row in rows}
    applied_reviews = [latest_reviews[item["id"]] for item in records if item["id"] in latest_reviews]
    counts = risk_panel_counts(records, latest_reviews, classify=event_metadata)
    coverage = research.coverage(snapshot, start=start, end=end)
    dimensions = [item for row in coverage for item in row["dimensions"]]
    return {
        "analysis_evidence_boundary": _analysis_evidence_boundary(),
        **counts,
        "supplemental_records_count": sum(bool(item.get("supplemental")) for item in records),
        "event_category_basis": "当前topic/dimension/日期范围全部记录的自动事件分类，可能含地图图例未展示类别；不应用地图图层、信号或地理点位筛选，不能冒充地图可见点数。",
        "signal_labels": SIGNALS,
        "counting_projection_version": "risk-panel-signals/v2",
        "review_evidence": {
            "applied_records_count": len(applied_reviews),
            "latest_applied_review_at": max((row["created_at"] or "" for row in applied_reviews), default="") or None,
            "basis": "仅统计当前筛选内实际应用的每条记录最新复核，不包含其他快照或筛选外记录。存在复核不证明两次读数差异由复核造成。",
        },
        "change_attribution": {
            "status": "not_established",
            "basis": "本响应是单次读取，未提供前后同口径读数与变更审计。不得将数值差异确定归因为人工复核、采集更新或代码修改；只能报告差异及原因尚未证实。投影版本标签没有部署时间，字段首次出现在对话的时间不是部署或回填时间，不支持原因排序。",
        },
        "signal_count_basis": "当前筛选范围内全部记录，采用与面板相同的自动分类及实际最新研究员复核；报道线索数量不是情景概率，也不是 scenarios.evidence_ids 的抽样长度。",
        "coverage_basis": "coverage 为全部主题的候选维度覆盖，排除 supplemental 补充报告；不随 topic/dimension 筛选。coverage[].count 是主题记录数，不是维度合计；一条记录可属于多个维度，dimensions[].count 不能加总。候选覆盖不是证据充分或数据完整。",
        "framework_candidate_coverage": {
            "covered": sum(item["count"] > 0 for item in dimensions),
            "total": len(dimensions),
            "scope": "all_topics",
        },
        "coverage": coverage,
    }


def _visible_snapshot(principal: Any, snapshot_id: str, working_id: str | None) -> bool:
    allowed = principal.allowed_snapshot_ids
    return "*" in allowed or snapshot_id in allowed or ("working" in allowed and snapshot_id == working_id)


def _record_summary(record_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record_item.get("id"),
        "title": record_item.get("title", ""),
        "summary": (record_item.get("summary") or record_item.get("text") or "")[:1200],
        "link": record_item.get("link") or record_item.get("url") or "",
        "topics": record_item.get("topics", []),
        "dimensions": record_item.get("dimensions", {}),
        "published_at": record_item.get("published_at"),
        "fetched_at": record_item.get("fetched_at"),
        "first_seen": record_item.get("first_seen"),
        "event_at": record_item.get("event_at"),
        "tier": record_item.get("tier"),
        "origin_id": record_item.get("origin_id"),
        "source": record_item.get("source") or record_item.get("source_name"),
    }


def _redacted_record(record_item: dict[str, Any]) -> dict[str, Any]:
    result = _record_summary(record_item)
    body = (record_item.get("text") or "").encode('utf8')
    result["text"] = body[:MAX_TEXT_BYTES].decode('utf8', errors='ignore')
    result['text_truncated'] = len(body) > MAX_TEXT_BYTES
    result['text_bytes_total'] = len(body)
    result['text_limit_bytes'] = MAX_TEXT_BYTES
    result["original_title"] = record_item.get("original_title") or result["title"]
    result["verification"] = record_item.get("verification")
    result["link_health"] = record_item.get("link_health")
    # Raw archive paths and crawler internals are intentionally excluded.
    return result


def _brief_projection(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "snapshot_id": row["snapshot_id"],
        "created_at": row["created_at"],
        "mode": row["mode"],
        "bytes": len(row["markdown"].encode("utf-8")),
    }


def _collector_projection(run: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded live projection without local paths or raw logs."""

    allowed = {
        "available",
        "source",
        "run_id",
        "run_kind",
        "run_status",
        "import_status",
        "snapshot_id",
        "updated_at",
        "started_at",
        "finished_at",
        "collector_count",
        "failed_count",
        "collectors",
    }
    result = {key: run.get(key) for key in allowed if key in run}
    result["live"] = True
    result["collection_snapshot_boundary"] = collection_snapshot_boundary()
    result["activity_assessment"] = {
        "process_running": "unknown",
        "events_after_latest_visible": "unknown",
        "no_updates_since_last_event": "not_established",
        "basis": "这是最近可见批次的账本，不是全量事件审计或心跳。可以说最后可见记录距取数约若干天，不能说此后若干天没有更新/新动作。",
    }
    result["interpretation_basis"] = "live 表示本次读取持久化运行账本，不是进程/调度器心跳。只能报告最近可见批次状态；有限 events 不能证明此后无新事件。批次或采集器 success 不代表所有来源成功，某来源本批失败不证明整个快照无该来源材料。records 字段未提供完整抓取计数口径，不得将默认零当成实际抓取零条；应与日志及产物区分。"
    collectors = []
    for item in (run.get("collectors") or [])[:MAX_COLLECTOR_EVENTS]:
        if not isinstance(item, dict):
            continue
        projected = {
            key: item.get(key)
            for key in (
                "id",
                "name",
                "group",
                "status",
                "stage",
                "elapsed",
                "started_at",
                "finished_at",
                "attempts",
                "records",
                "outputs",
                "last_success",
                "last_success_at",
                "last_run_at",
                "source_health",
                "error_type",
                "error_reason",
                "suggested_action",
            )
            if key in item
        }
        if "error_message" in item:
            projected["error_message"] = _sanitize_runtime_text(item.get("error_message"), limit=1000)
        if "error_detail" in item:
            projected["error_detail"] = _sanitize_runtime_text(item.get("error_detail"), limit=500)
        collectors.append(projected)
    result["collectors"] = collectors
    return result


def _sanitize_runtime_text(value: Any, *, limit: int = 500) -> str | None:
    message = str(value or "")
    # Runtime log payloads may contain local paths, query credentials, or full
    # stdout/stderr. Keep a short human-readable hint but never forward those
    # raw fields through MCP.
    message = re.sub(
        r"(?i)(api[_-]?key|key|token|secret|password)=([^&\s]+)",
        lambda match: f"{match.group(1)}=<redacted>",
        message,
    )
    message = re.sub(r"/(?:Users|private|tmp|var)/[^\s\n]+", "<local-path>", message)
    return message[:limit] if message else None


def _redacted_collector_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: event.get(key)
        for key in ("id", "run_id", "collector_id", "event_type", "status", "created_at")
        if key in event
    } | {"message": _sanitize_runtime_text(event.get("message"))}


def _terms(query: str) -> list[str]:
    return re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fff]{2,}", query.lower())


def _search_records(records: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    if not query.strip():
        return sorted(records, key=lambda item: item.get("published_at") or "", reverse=True)
    terms = _terms(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in records:
        haystack = f"{item.get('title', '')} {item.get('text', '')} {item.get('summary', '')}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score:
            scored.append((score, item))
    return [item for _, item in sorted(scored, key=lambda pair: (pair[0], pair[1].get("published_at") or ""), reverse=True)]


def _context_principal(ctx: Context) -> Any:
    return principal_from_request(ctx.request_context.request)


async def _run_tool(*, ctx: Context, tool: str, handler: Callable) -> str:
    # SQLite reads, JSON snapshot decoding and research projections are sync.
    # Keep them off the ASGI loop so health/identity/MCP requests can progress.
    return await run_in_threadpool(_execute_tool, ctx=ctx, tool=tool, handler=handler)


def _analysis_metric_projection(metric: dict[str, Any]) -> dict[str, Any]:
    """Overview facts, not a repeated unbounded time-series archive."""
    fields = ('id', 'series', 'name', 'unit', 'frequency', 'scope', 'topic', 'dimension', 'bucket',
              'current', 'previous', 'delta', 'delta_unit', 'pct', 'freshness',
              'quality_issue', 'comparison_step', 'method', 'boundary', 'selection_reason')
    result = {key: metric.get(key) for key in fields}
    source_ids = metric.get('source_ids') or []
    result.update(source_ids=source_ids[:50], source_ids_count=len(source_ids),
                  source_ids_truncated=len(source_ids)>50,
                  history_included=False, history_points_count=len(metric.get('history') or []),
                  details_tool='geo_metric_series', semantics=metric_semantics(metric))
    return result


def _execute_tool(
    *,
    ctx: Context,
    tool: str,
    handler: Callable[[Any, str], tuple[Any, dict[str, Any], list[dict[str, Any]]]],
) -> str:
    request_id = request_id_from_context(ctx)
    started = monotonic()
    principal = None
    snapshot_id: str | None = None
    try:
        principal = _context_principal(ctx)
        data, provenance, warnings = handler(principal, request_id)
        audit_event_id = record(
            request_id=request_id,
            principal_id=principal.principal_id,
            agent_id=principal.agent_id,
            tool=tool,
            snapshot_id=provenance.get("snapshot_id") if provenance else snapshot_id,
            status="ok",
            started=started,
        )
        response = success_envelope(
            data=data,
            request_id=request_id,
            tool=tool,
            provenance=provenance,
            warnings=warnings,
            audit_event_id=audit_event_id,
        )
        logger.info("mcp_projection tool=%s response_bytes=%d total_duration_ms=%d",
                    tool, len(response.encode('utf-8')), round((monotonic()-started)*1000))
        return response
    except MCPError as exc:
        audit_event_id = record(
            request_id=request_id,
            principal_id=principal.principal_id if principal else "unauthenticated",
            agent_id=principal.agent_id if principal else "",
            tool=tool,
            snapshot_id=snapshot_id,
            status=exc.code,
            started=started,
        )
        error = exc.as_dict(request_id=request_id, tool=tool)
        error["audit_event_id"] = audit_event_id
        return json.dumps(error, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        logger.exception("MCP tool failed: %s", tool)
        audit_event_id = record(
            request_id=request_id,
            principal_id=principal.principal_id if principal else "unknown",
            agent_id=principal.agent_id if principal else "",
            tool=tool,
            snapshot_id=snapshot_id,
            status="GEO_INTERNAL_ERROR",
            started=started,
        )
        error = MCPError("GEO_INTERNAL_ERROR", "GeoAgent MCP 内部错误").as_dict(request_id=request_id, tool=tool)
        error["audit_event_id"] = audit_event_id
        return json.dumps(error, ensure_ascii=False, separators=(",", ":"))


def create_server(*, auth_config: AuthConfig | None = None, project_transport: bool = False) -> FastMCP:
    auth_config = auth_config or AuthConfig.from_env()
    server = FastMCP(
        "GeoAgent MCP",
        instructions="只读访问 GeoAgent 快照、证据和业务分析。所有结果带 snapshot provenance。本周默认按所选快照导入日所在周周一至导入日，见 relative_time_basis，不按今天猜测。区分事实、规则条件和假设；描述某字段可以更新，不证明本次发生过更新。没有前后同口径数据及变更证据，不得确定归因或对原因排序。候选覆盖不等于数据完整，情景候选证据不等于已核验反证。冻结运行记录不能证明之后没有新事件；当前管线必须读取 live 状态及其时间。",
        host=os.getenv("GEOAGENT_MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("GEOAGENT_MCP_PORT", "8401")),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        # The project is served behind deployment-owned hostnames. Authentication
        # is mandatory Bearer (not ambient cookies), so do not use the SDK's
        # loopback-only Host allowlist for the shared project endpoint.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False) if project_transport else None,
    )
    server._geo_auth_config = auth_config  # type: ignore[attr-defined]

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "geoagent-mcp", "protocol_version": "geo-mcp/v1"})

    @server.tool(name="geo_snapshot_list", description="列出当前 principal 可见的 GeoAgent 快照版本和统计。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_snapshot_list(ctx: Context) -> str:
        tool = "geo_snapshot_list"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            authorize(principal, tool=tool)
            summaries = store.snapshot_summaries()
            working_id = store.get_working_snapshot_id()
            latest_id = _latest_id(summaries)
            visible = [
                {
                    **summary,
                    "is_working_snapshot": summary["id"] == working_id,
                    "is_latest_snapshot": summary["id"] == latest_id,
                }
                for summary in summaries
                if _visible_snapshot(principal, summary["id"], working_id)
            ]
            return {"snapshots": visible}, list_provenance(working_snapshot_id=working_id, latest_snapshot_id=latest_id), []

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_snapshot_get", description="读取一个可见快照的轻量元数据，不返回全量 records。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_snapshot_get(snapshot_id: SnapshotId, ctx: Context) -> str:
        tool = "geo_snapshot_get"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            working_id = store.get_working_snapshot_id()
            authorize(principal, tool=tool, snapshot_id=snapshot_id, working_snapshot_id=working_id)
            try:
                snapshot = store.get_snapshot(snapshot_id)
            except KeyError as exc:
                raise MCPError("GEO_SNAPSHOT_NOT_FOUND", "快照不存在", {"snapshot_id": snapshot_id}) from exc
            summaries = store.snapshot_summaries()
            summary = next((item for item in summaries if item["id"] == snapshot_id), None)
            if summary is None:
                raise MCPError("GEO_SNAPSHOT_NOT_FOUND", "快照不存在", {"snapshot_id": snapshot_id})
            data = {"snapshot": summary, "records_basis": "records为全快照归并后的记录数；不是当前主题/维度/日期筛选的面板记录数。面板计数请读取geo_workspace_summary。"}
            return data, snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_id(summaries)), []

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_workspace_summary", description="读取风险监测面板：升级相关/持续相关/缓和相关/待研判报道数量 signal_counts、范围内记录数 records_count、全框架候选维度覆盖，以及指标、资产和情景摘要。问‘风险概览、各类线索多少、维度覆盖多少、还缺什么数据’时使用。review_evidence 仅证明本次应用的复核，change_attribution 限定差异原因尚未证实，不能把可更新机制当成实际变更原因。报道计数不是 scenarios.evidence_ids 的有限候选样本数量，也不是情景概率。coverage 排除补充报告且为全部主题，coverage[].count 是主题记录数而非维度合计；records_count 遵循 topic/dimension/日期筛选。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_workspace_summary(
        snapshot_id: SnapshotId,
        topic: Topic = "all",
        dimension: Dimension = "",
        start: Start = "",
        end: End = "",
        limit: Limit = 20,
        compact: Annotated[bool, Field(description="true仅返回概览，完整资产/情景用geo_analysis_view按需读取；false保留旧版完整字段。建议概览使用true。")]=False,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_workspace_summary"
        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            _validate_filters(topic, dimension, start, end)
            page_limit = clamp_limit(limit)
            working_id = store.get_working_snapshot_id()
            authorize(principal, tool=tool, snapshot_id=snapshot_id, working_snapshot_id=working_id, topic=topic if topic != "all" else None, dimension=dimension or None)
            try:
                snapshot = store.get_snapshot(snapshot_id)
            except KeyError as exc:
                raise MCPError("GEO_SNAPSHOT_NOT_FOUND", "快照不存在", {"snapshot_id": snapshot_id}) from exc
            metrics = research.metrics(snapshot, topic=topic, dimension=dimension, start=start, end=end)
            analysis = research.analysis(snapshot, topic=topic, dimension=dimension, start=start, end=end)
            summary = {
                **_workspace_counts(snapshot, topic, dimension, start, end),
                "asset_data_gaps": [
                    {"id": item["id"], "name": item["name"], "missing_data": item.get("missing_data", "")}
                    for item in analysis.get("assets", [])
                ],
                "metric_quality_basis": "有效零值不是缺失；隔离值不是不存在原始数据。shared_market/美国整体背景按业务主题映射入选，topic=null 不能推断未绑定或不相关。排除 supplemental 是统计范围，不是缺数据；少量来源抽查不能代表全体来源单一。目录仅枚举当前范围可用序列，不在目录不证明没有原始数据或工具不能读取；不能把全主题 warnings 无标注地当成当前主题缺口。",
                "query_scope": {"topic": topic, "dimension": dimension, "start": start, "end": end},
                "quality_warning_scope": {"topic": topic, "dimension": dimension, "start": start, "end": end, "count": len(analysis.get("data_warnings", []))},
                "metrics": [
                    {
                        "id": item.get("id"),
                        "series": item.get("series"),
                        "name": item.get("name"),
                        "unit": item.get("unit"),
                        "current": item.get("current"),
                        "previous": item.get("previous"),
                        "delta": item.get("delta"),
                        "pct": item.get("pct"),
                        "freshness": item.get("freshness"),
                        "source_ids": item.get("source_ids", [])[:page_limit],
                    }
                    for item in metrics[:page_limit]
                ],
                "assets": ([{k: item.get(k) for k in ('id', 'name', 'direction', 'assessment_kind')} for item in analysis.get("assets", [])[:page_limit]] if compact else analysis.get('assets', [])),
                "scenarios": ([{k: item.get(k) for k in ('id', 'name', 'status')} for item in analysis.get("scenarios", [])[:page_limit]] if compact else analysis.get('scenarios', [])),
                "projection": "compact-v1" if compact else "legacy-full",
                "details_tool": "geo_analysis_view",
                "result_limits": {"limit": page_limit, "total_metrics": len(metrics), "total_assets": len(analysis.get('assets', [])), "total_scenarios": len(analysis.get('scenarios', [])), "basis": "metrics返回前limit项；compact=true时资产/情景也返回前limit项，否则保留旧版完整字段。完整规则/候选按需读取geo_analysis_view；coverage为全主题独立统计。"},
                "observations_count": len(metrics),
            }
            warnings = analysis.get("data_warnings", [])
            return summary, snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_snapshot_id()), warnings

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_metric_catalog", description="列出看板可用指标目录，不返回完整时间序列；本周/区间变化优先geo_metric_changes一次批量计算，含义和有界历史用geo_metric_series。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_metric_catalog(
        snapshot_id: SnapshotId,
        topic: Topic = "all",
        dimension: Dimension = "",
        start: Start = "",
        end: End = "",
        offset: Offset = 0,
        limit: Limit = 20,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_metric_catalog"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            _validate_filters(topic, dimension, start, end)
            if offset < 0:
                raise MCPError("GEO_INVALID_OFFSET", "offset 不能为负数")
            page_limit = clamp_limit(limit)
            working_id = store.get_working_snapshot_id()
            authorize(
                principal,
                tool=tool,
                snapshot_id=snapshot_id,
                working_snapshot_id=working_id,
                topic=topic if topic != "all" else None,
                dimension=dimension or None,
            )
            try:
                snapshot = store.get_snapshot(snapshot_id)
            except KeyError as exc:
                raise MCPError("GEO_SNAPSHOT_NOT_FOUND", "快照不存在", {"snapshot_id": snapshot_id}) from exc
            metrics = research.metrics(snapshot, topic=topic, dimension=dimension, start=start, end=end)
            page = metrics[offset : offset + page_limit]
            catalog = []
            for item in page:
                current = item.get("current") or {}
                catalog.append(
                    {
                        "id": item.get("id"),
                        "series": item.get("series") or item.get("id"),
                        "name": item.get("name"),
                        "unit": item.get("unit"),
                        "frequency": item.get("frequency"),
                        "scope": item.get("scope"),
                        "topic": item.get("topic"),
                        "dimension": item.get("dimension"),
                        "observed_at": item.get("observed_at") or current.get("date"),
                        "current": current,
                        "freshness": item.get("freshness"),
                        "source_ids": (item.get("source_ids") or [])[:10],
                    }
                )
            return (
                {
                    "metrics": catalog,
                    "offset": offset,
                    "limit": page_limit,
                    "has_more": offset + page_limit < len(metrics),
                    "total": len(metrics),
                },
                snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_snapshot_id()),
                [],
            )

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_metric_series", description="读取快照内指定指标的有界时间序列和程序计算摘要。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_metric_series(
        snapshot_id: SnapshotId,
        series: Series,
        topic: Topic = "all",
        dimension: Dimension = "",
        start: Start = "",
        end: End = "",
        max_points: MaxPoints = 50,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_metric_series"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            _validate_filters(topic, dimension, start, end)
            if not series.strip():
                raise MCPError("GEO_METRIC_INVALID", "series 不能为空")
            point_limit = clamp_limit(max_points)
            working_id = store.get_working_snapshot_id()
            authorize(
                principal,
                tool=tool,
                snapshot_id=snapshot_id,
                working_snapshot_id=working_id,
                topic=topic if topic != "all" else None,
                dimension=dimension or None,
            )
            try:
                snapshot = store.get_snapshot(snapshot_id)
            except KeyError as exc:
                raise MCPError("GEO_SNAPSHOT_NOT_FOUND", "快照不存在", {"snapshot_id": snapshot_id}) from exc
            metrics = research.metrics(snapshot, topic=topic, dimension=dimension, start=start, end=end)
            metric = next((item for item in metrics if item.get("series") == series), None)
            if metric is None:
                metric = next((item for item in metrics if item.get("id") == series), None)
            if metric is None:
                raise MCPError("GEO_METRIC_NOT_FOUND", "快照内不存在该指标", {"series": series, "snapshot_id": snapshot_id})
            history = metric.get("history") or []
            bounded_history = history[-point_limit:]
            data = {
                "metric": {
                    "id": metric.get("id"),
                    "series": metric.get("series") or metric.get("id"),
                    "name": metric.get("name"),
                    "unit": metric.get("unit"),
                    "frequency": metric.get("frequency"),
                    "scope": metric.get("scope"),
                    "topic": metric.get("topic"),
                    "dimension": metric.get("dimension"),
                    "observed_at": metric.get("observed_at"),
                    "current": metric.get("current"),
                    "previous": metric.get("previous"),
                    "delta": metric.get("delta"),
                    "delta_unit": metric.get("delta_unit"),
                    "pct": metric.get("pct"),
                    "freshness": metric.get("freshness"),
                    "status": metric.get("status"),
                    "semantics": metric_semantics(metric),
                    "interval_change": interval_change(metric, start, end),
                    "source_ids": (metric.get("source_ids") or [])[:10],
                    "history": bounded_history,
                    "history_truncated": len(history) > len(bounded_history),
                }
            }
            warnings = []
            if len(history) > len(bounded_history):
                warnings.append({"code": "GEO_OUTPUT_TRUNCATED", "message": "时间序列已按 max_points 截断", "max_points": point_limit})
            return (
                data,
                snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_snapshot_id()),
                warnings,
            )

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_evidence_search", description="在指定快照内分页检索证据摘要，使用关键词、BM25、字符语义近似、实体、时间、来源等级和来源多样性混合重排序；关键候选须再用geo_evidence_get读取正文。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_evidence_search(
        snapshot_id: SnapshotId,
        q: Query = "",
        topic: Topic = "all",
        dimension: Dimension = "",
        start: Start = "",
        end: End = "",
        offset: Offset = 0,
        limit: Limit = 20,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_evidence_search"
        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            _validate_filters(topic, dimension, start, end)
            if offset < 0:
                raise MCPError("GEO_INVALID_OFFSET", "offset 不能为负数")
            page_limit = clamp_limit(limit)
            working_id = store.get_working_snapshot_id()
            authorize(principal, tool=tool, snapshot_id=snapshot_id, working_snapshot_id=working_id, topic=topic if topic != "all" else None, dimension=dimension or None)
            try:
                snapshot = store.get_snapshot(snapshot_id)
            except KeyError as exc:
                raise MCPError("GEO_SNAPSHOT_NOT_FOUND", "快照不存在", {"snapshot_id": snapshot_id}) from exc
            records = research.filtered_records(snapshot, topic, dimension, start, end)
            matched = search_evidence(records, q)
            reviews = review_index(snapshot_id)
            page = matched[offset : offset + page_limit]
            return (
                {"analysis_evidence_boundary": _analysis_evidence_boundary(), "query_scope": {"snapshot_id": snapshot_id, "topic": topic, "dimension": dimension, "start": start, "end": end, "q": q}, "search_basis": "关键词、BM25、字符语义近似、实体、时间、来源等级和来源多样性混合重排序；来源等级不等于事实核验。", "records": [{**_record_summary(item), **evidence_semantics(item, reviews.get(item['id']))} for item in page], "offset": offset, "limit": page_limit, "has_more": offset + page_limit < len(matched), "total": len(matched)},
                snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_snapshot_id()),
                [],
            )

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_evidence_get", description="读取指定快照内单条证据的正文、来源、发布/采集时间与核验状态。报道及单方通报不是独立核实事实；单条或有限样本不能证明整个快照缺少某类证据，见 analysis_evidence_boundary。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_evidence_get(snapshot_id: SnapshotId, record_id: RecordId, ctx: Context) -> str:
        tool = "geo_evidence_get"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            working_id = store.get_working_snapshot_id()
            authorize(principal, tool=tool, snapshot_id=snapshot_id, working_snapshot_id=working_id)
            try:
                snapshot = store.get_snapshot(snapshot_id)
            except KeyError as exc:
                raise MCPError("GEO_SNAPSHOT_NOT_FOUND", "快照不存在", {"snapshot_id": snapshot_id}) from exc
            item = next((record_item for record_item in snapshot.get("records", []) if record_item.get("id") == record_id), None)
            if item is None:
                raise MCPError("GEO_EVIDENCE_NOT_FOUND", "快照内不存在该证据", {"record_id": record_id, "snapshot_id": snapshot_id})
            detail = {**_redacted_record(item), **evidence_semantics(item, review_index(snapshot_id).get(record_id))}
            detail['link_health'] = {k: v for k, v in link_health(snapshot_id, item.get('url', '')).items() if k in ('status', 'url', 'http_status', 'checked_at', 'final_url')}
            detail['link_health_basis'] = '与台账详情读取同一份链接检查记录，不在调用时发起网络检查；链接可访问不等于正文事实已核实。核验时点见checked_at，未检查不等于链接失效。'
            return {"analysis_evidence_boundary": _analysis_evidence_boundary(), "record": detail}, snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_snapshot_id()), []

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_analysis_view", description="读取快照内五类资产和三类情景的结构化条件研判。对照业务面板时须保持同一 topic、dimension、start、end；用户未指定日期或维度时不要自行收窄。不同筛选的 topic_paths/status 不可拼接成同一面板结论。asset_overview 是精简五类资产信息，完整 assets 含更多材料。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_analysis_view(
        snapshot_id: SnapshotId,
        topic: Topic = "all",
        dimension: Dimension = "",
        start: Start = "",
        end: End = "",
        limit: Limit = 20,
        asset_id: Annotated[str, Field(description="只读取指定资产：equity权益、rates利率债、credit信用债、commodity商品、fx外汇；空串全部。") ] = "",
        scenario_id: Annotated[str, Field(description="只读取指定情景：sustain持续消耗、escalate升级扩散、ease缓和收束；空串全部。") ] = "",
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_analysis_view"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            _validate_filters(topic, dimension, start, end)
            page_limit = clamp_limit(limit)
            working_id = store.get_working_snapshot_id()
            authorize(
                principal,
                tool=tool,
                snapshot_id=snapshot_id,
                working_snapshot_id=working_id,
                topic=topic if topic != "all" else None,
                dimension=dimension or None,
            )
            try:
                snapshot = store.get_snapshot(snapshot_id)
            except KeyError as exc:
                raise MCPError("GEO_SNAPSHOT_NOT_FOUND", "快照不存在", {"snapshot_id": snapshot_id}) from exc
            result = research.analysis(snapshot, topic=topic, dimension=dimension, start=start, end=end)
            if asset_id and asset_id not in {a['id'] for a in result['assets']}:
                raise MCPError('GEO_ASSET_NOT_FOUND', '资产ID不存在')
            if scenario_id and scenario_id not in {s['id'] for s in result['scenarios']}:
                raise MCPError('GEO_SCENARIO_NOT_FOUND', '情景ID不存在')
            result['assets'] = [a for a in result['assets'] if not asset_id or a['id'] == asset_id]
            result['scenarios'] = [s for s in result['scenarios'] if not scenario_id or s['id'] == scenario_id]
            for scenario in result['scenarios']:
                scenario['evidence_condition_links'] = [
                    {'record_id': rid, 'relation': 'candidate_only', 'verification': 'not_evaluated',
                     'condition': scenario['condition'], 'counter_condition': scenario['counter'],
                     'basis': '检索候选，不构成条件已满足或反证已成立的判定；须读取正文独立核验。'}
                    for rid in scenario.get('evidence_ids', [])
                ]
            data = {
                "analysis_evidence_boundary": _analysis_evidence_boundary(),
                "query_scope": {"topic": topic, "dimension": dimension, "start": start, "end": end},
                "limit_basis": "limit 只截取返回的资产/情景/指标列表项，不参与 topic_paths 匹配计算；列表缺项不是该资产无证据。前后路径状态不同必须核对相同筛选及实际回执，不能仅据 limit 不同断言原因。",
                "asset_overview": [
                    {key: item.get(key) for key in ("id", "name", "direction", "assessment_kind", "condition", "counter", "missing_data", "topic_paths")}
                    for item in (result.get("assets") or [])[:page_limit]
                ],
                "assets": (result.get("assets") or [])[:page_limit],
                "scenarios": (result.get("scenarios") or [])[:page_limit],
                "rule_version": result.get("rule_version", RULE_VERSION),
                "important_metrics": [_analysis_metric_projection(m) for m in (result.get("important_metrics") or [])[:page_limit]],
                "metric_projection_basis": "概览保留首末单期观测、变化、质量、语义和来源；不附完整历史。history_points_count不是连续覆盖证明，序列及区间详情用geo_metric_series/geo_metric_changes。",
                "total_assets": len(result.get("assets") or []),
                "total_scenarios": len(result.get("scenarios") or []),
            }
            return (
                data,
                snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_snapshot_id()),
                result.get("data_warnings") or [],
            )

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_brief_list", description="列出 principal 可见的已落盘研究简报元数据。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_brief_list(
        snapshot_id: SnapshotId = "",
        offset: Offset = 0,
        limit: Limit = 20,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_brief_list"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            if offset < 0:
                raise MCPError("GEO_INVALID_OFFSET", "offset 不能为负数")
            page_limit = clamp_limit(limit)
            working_id = store.get_working_snapshot_id()
            if snapshot_id:
                authorize(principal, tool=tool, snapshot_id=snapshot_id, working_snapshot_id=working_id)
            else:
                authorize(principal, tool=tool)
            summaries = store.snapshot_summaries()
            latest_id = _latest_id(summaries)
            visible_ids = {
                item["id"]
                for item in summaries
                if _visible_snapshot(principal, item["id"], working_id)
            }
            with store.db() as connection:
                if snapshot_id:
                    rows = connection.execute(
                        "SELECT id,snapshot_id,created_at,mode,markdown FROM briefs WHERE snapshot_id=? ORDER BY created_at DESC",
                        (snapshot_id,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT id,snapshot_id,created_at,mode,markdown FROM briefs ORDER BY created_at DESC"
                    ).fetchall()
            rows = [row for row in rows if row["snapshot_id"] in visible_ids]
            page = rows[offset : offset + page_limit]
            provenance = list_provenance(working_snapshot_id=working_id, latest_snapshot_id=latest_id)
            return (
                {
                    "briefs": [_brief_projection(row) for row in page],
                    "offset": offset,
                    "limit": page_limit,
                    "has_more": offset + page_limit < len(rows),
                    "total": len(rows),
                },
                provenance,
                [],
            )

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_brief_get", description="读取已落盘研究简报的受限正文和 snapshot lineage。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_brief_get(brief_id: BriefId, max_bytes: MaxBytes = MAX_BRIEF_BYTES, *, ctx: Context) -> str:
        tool = "geo_brief_get"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            if max_bytes < 1 or max_bytes > MAX_BRIEF_BYTES:
                raise MCPError("GEO_LIMIT_EXCEEDED", f"max_bytes 必须在 1 到 {MAX_BRIEF_BYTES} 之间", {"maximum": MAX_BRIEF_BYTES})
            with store.db() as connection:
                row = connection.execute(
                    "SELECT id,snapshot_id,created_at,mode,markdown FROM briefs WHERE id=?",
                    (brief_id,),
                ).fetchone()
            if row is None:
                raise MCPError("GEO_BRIEF_NOT_FOUND", "研究简报不存在", {"brief_id": brief_id})
            working_id = store.get_working_snapshot_id()
            authorize(principal, tool=tool, snapshot_id=row["snapshot_id"], working_snapshot_id=working_id)
            encoded = localize_brief_markdown(row["markdown"]).encode("utf-8")
            truncated = len(encoded) > max_bytes
            markdown = encoded[:max_bytes].decode("utf-8", errors="ignore")
            warnings = [{"code": "GEO_OUTPUT_TRUNCATED", "message": "简报正文已按 max_bytes 截断", "max_bytes": max_bytes}] if truncated else []
            snapshot = store.get_snapshot(row["snapshot_id"])
            return (
                {
                    "brief": {
                        "id": row["id"],
                        "snapshot_id": row["snapshot_id"],
                        "created_at": row["created_at"],
                        "mode": row["mode"],
                        "markdown": markdown,
                        "truncated": truncated,
                        "text_basis": "研究简报看板的中文展示正文；原始归档不修改，来源标签翻译可更新。",
                    }
                },
                snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_snapshot_id()),
                warnings,
            )

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_collector_status", description="读取采集运行态的有界 live 投影，不混入快照 provenance。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_collector_status(
        run_id: RunId = "",
        collector_id: CollectorId = "",
        limit: Limit = 20,
        compare_run_id: Annotated[str, Field(description="可选前一批次ID，从geo_collector_runs获取；与run_id逐项对比，不推断差异原因。空串不比较。")]='',
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_collector_status"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            page_limit = clamp_limit(limit, maximum=MAX_COLLECTOR_EVENTS)
            authorize(principal, tool=tool)
            runs_root = store.data_dir() / "collection-runs"
            registry_path = project_root() / "collectors" / "collectors.json"
            try:
                load_registry(registry_path)
                runs = load_runs(runs_root, registry_path, limit=max(page_limit, 20), run_id=run_id)
            except (OSError, ValueError, KeyError) as exc:
                raise MCPError("GEO_COLLECTOR_UNAVAILABLE", "采集运行态暂不可用", {"reason": type(exc).__name__}) from exc
            if run_id:
                runs = [item for item in runs if item.get("run_id") == run_id]
            if not runs:
                if run_id:
                    raise MCPError("GEO_RUN_NOT_FOUND", "采集运行记录不存在", {"run_id": run_id})
                runs = [{"available": False, "source": "none", "run_id": None, "collectors": []}]
            run = runs[0]
            if collector_id:
                run = {**run, "collectors": [item for item in (run.get("collectors") or []) if item.get("id") == collector_id]}
            projected = _collector_projection(run)
            if compare_run_id:
                previous = load_runs(runs_root, registry_path, limit=1, run_id=compare_run_id)
                if not previous:
                    raise MCPError('GEO_RUN_NOT_FOUND', '对比批次不存在', {'run_id': compare_run_id})
                old = previous[0]
                if collector_id:
                    old = {**old, 'collectors': [c for c in old.get('collectors', []) if c.get('id') == collector_id]}
                projected['comparison'] = collector_comparison(_collector_projection(old), projected)
            if projected.get("run_id"):
                event_result = load_ledger_events(runs_root, projected["run_id"], collector_id=collector_id or None, limit=page_limit)
                projected["events"] = [
                    _redacted_collector_event(event)
                    for event in event_result.get("events", [])[:page_limit]
                    if isinstance(event, dict)
                ]
            else:
                projected["events"] = []
            return projected, {"kind": "live", "source": "collector-monitor", "live": True}, []

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(
        name="geo_internal_message_search",
        description="检索本地去重保存的华泰宏观消息短摘要。支持关键词、日期、群聊显示名、标签和文本分类；每页最多20条，先筛选再用geo_internal_message_get按ID读取少量详情，不要遍历全库正文。结果不等于已核验事实。",
        annotations={"readOnlyHint": True, "destructiveHint": False},
        structured_output=False,
    )
    async def geo_internal_message_search(
        query: Annotated[str, Field(description="标题、摘要、上游AI摘要、标签或群聊显示名中的原样子串；空串按时间列出。")]= "",
        start: Start = "",
        end: End = "",
        group_name: InternalFilter = "",
        tag: InternalFilter = "",
        text_class: InternalFilter = "",
        offset: Offset = 0,
        limit: InternalMessageLimit = 20,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_internal_message_search"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            authorize(principal, tool=tool)
            try:
                data = search_internal_messages(
                    query=query, start=start, end=end, group_name=group_name, tag=tag,
                    text_class=text_class, offset=offset, limit=clamp_limit(limit, maximum=20),
                )
            except ValueError as exc:
                raise MCPError("GEO_INVALID_FILTER", str(exc)) from exc
            data["basis"] = "读取独立本地消息数据库的短摘要；消息时间与首次/最后采集时间分开，上游AI摘要不作为事实核验。完整内容只按搜索结果ID调用geo_internal_message_get。"
            return data, {"kind": "live", "source": "ht-macro-local-db", "live": True}, []

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(
        name="geo_internal_message_get",
        description="按公开记录ID读取一条本地华泰宏观消息，不返回上游账号、Token、群聊内部ID或接收人内部ID。",
        annotations={"readOnlyHint": True, "destructiveHint": False},
        structured_output=False,
    )
    async def geo_internal_message_get(message_id: InternalMessageId, *, ctx: Context) -> str:
        tool = "geo_internal_message_get"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            authorize(principal, tool=tool)
            message = get_internal_message(message_id)
            if message is None:
                raise MCPError("GEO_INTERNAL_MESSAGE_NOT_FOUND", "内部消息不存在", {"message_id": message_id})
            return {"message": message}, {"kind": "live", "source": "ht-macro-local-db", "live": True}, []

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(
        name="geo_internal_message_stats",
        description="读取华泰宏观本地消息库的记录数、最新消息、最近采集批次、文本分类和标签统计。",
        annotations={"readOnlyHint": True, "destructiveHint": False},
        structured_output=False,
    )
    async def geo_internal_message_stats(ctx: Context) -> str:
        tool = "geo_internal_message_stats"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            authorize(principal, tool=tool)
            return internal_message_stats(), {"kind": "live", "source": "ht-macro-local-db", "live": True}, []

        return await _run_tool(ctx=ctx, tool=tool, handler=handler)

    def scoped_snapshot(principal, tool, snapshot_id, topic='all', dimension='', start='', end=''):
        _validate_filters(topic, dimension, start, end)
        working = store.get_working_snapshot_id()
        authorize(principal, tool=tool, snapshot_id=snapshot_id, working_snapshot_id=working,
                  topic=topic if topic != 'all' else None, dimension=dimension or None)
        try:
            snapshot = store.get_snapshot(snapshot_id)
        except KeyError as exc:
            raise MCPError('GEO_SNAPSHOT_NOT_FOUND', '快照不存在', {'snapshot_id': snapshot_id}) from exc
        return snapshot, snapshot_provenance(snapshot, working_snapshot_id=working, latest_snapshot_id=_latest_snapshot_id())

    @server.tool(description="回答风险地图主要事件、哪些地区关联报道多、地图数量为何与右侧不同。保持快照/主题/日期范围，支持图层和信号；地点是报道关联区域，不证明部署或事件事实，不读取当前地图视口。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_risk_map(snapshot_id: SnapshotId, topic: Topic='all', dimension: Dimension='', start: Start='', end: End='', categories: Annotated[list[str] | None, Field(description='事件图层机器值：military军事、diplomacy外交、policy政策、energy能源、support援助、domestic社会；null为全部六类，空数组为关闭全部。')]=None, signal: Annotated[str, Field(description='信号：all全部、escalation升级、sustain持续、easing缓和、unknown待研判；应用人工复核后筛选。')]='all', region: Annotated[str, Field(description='地点中文标签子串，例如霍尔木兹或伊朗；空串全部。不是地图视口、国家边界或全文检索。')]='', limit: Limit=20, *, ctx: Context) -> str:
        def handler(principal, _):
            snapshot, provenance = scoped_snapshot(principal, 'geo_risk_map', snapshot_id, topic, dimension, start, end)
            layers = list(CATEGORIES) if categories is None else categories
            if any(c not in CATEGORIES for c in layers) or signal not in {'all', *SIGNALS}:
                raise MCPError('GEO_INVALID_FILTER', '非法事件图层或信号', {'categories': list(CATEGORIES), 'signals': ['all', *SIGNALS]})
            size = clamp_limit(limit)
            data = map_projection(research.filtered_records(snapshot, topic, dimension, start, end), review_index(snapshot_id), layers, signal, region, size)
            data['query_scope'] = {'snapshot_id': snapshot_id, 'topic': topic, 'dimension': dimension, 'start': start, 'end': end, 'categories': layers, 'signal': signal, 'region': region}
            return data, provenance, []
        return await _run_tool(ctx=ctx, tool='geo_risk_map', handler=handler)

    @server.tool(description="发现业务主题/跟踪维度中文名与机器值，以及看板到工具的问答路径；参数不明时先查此工具，不提供具体快照数值。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_business_catalog(ctx: Context) -> str:
        def handler(principal, _):
            authorize(principal, tool='geo_business_catalog')
            return {'topics': [{'id': t['id'], 'name': t['name'], 'dimensions': [{'id': k, 'name': v} for k, v in t['dimensions']]} for t in TOPICS],
                    'questions': {'本周哪些指标发生变化': ['geo_metric_changes', 'geo_metric_series'],
                                  '地图主要事件与关联地区': ['geo_risk_map', 'geo_evidence_get'],
                                  '当前判断有哪些反证': ['geo_analysis_view', 'geo_evidence_search', 'geo_evidence_get'],
                                  '内部宏观群近期提到什么': ['geo_internal_message_search', 'geo_internal_message_get'],
                                  '还缺什么数据': ['geo_workspace_summary', 'geo_source_health', 'geo_metric_catalog'],
                                  '快照版本列表（不代表当前页面筛选）': ['geo_snapshot_list']},
                    'context_priority': '用户明确指定优先，其次当前看板上下文；不知道当前范围时先澄清，不静默切换latest/all。'}, {}, []
        return await _run_tool(ctx=ctx, tool='geo_business_catalog', handler=handler)

    @server.tool(description="回答本周/指定区间哪些指标发生变化：程序按区间首末有效观测计算，默认快照导入周。返回单位、日期、质量和分页；单期比较与区间变化不同，解释详情用geo_metric_series。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_metric_changes(snapshot_id: SnapshotId, topic: Topic = 'all', dimension: Dimension = '', start: Start = '', end: End = '', offset: Offset = 0, limit: Limit = 20, *, ctx: Context) -> str:
        def handler(principal, _):
            snapshot, provenance = scoped_snapshot(principal, 'geo_metric_changes', snapshot_id, topic, dimension, start, end)
            week = provenance['relative_time_basis']['this_week']
            lower, upper = (start, end) if start or end else ((week or {}).get('start', ''), (week or {}).get('end', ''))
            validate_dates(lower, upper)
            if offset < 0:
                raise MCPError('GEO_INVALID_OFFSET', 'offset不能为负数')
            size = clamp_limit(limit)
            rows = research.metrics(snapshot, topic, dimension, lower, upper)
            changes = [interval_change(m, lower, upper) for m in rows]
            return {'query_scope': {'topic': topic, 'dimension': dimension, 'start': lower, 'end': upper},
                    'summary': {'comparable': sum(c['status']=='comparable' for c in changes), 'changed': sum(c['status']=='comparable' and c['delta']!=0 for c in changes), 'unchanged': sum(c['status']=='comparable' and c['delta']==0 for c in changes), 'insufficient_observations': sum(c['status']=='insufficient_observations' for c in changes), 'quality_excluded': sum(c['status']=='quality_excluded' for c in changes), 'basis': '当前范围全部指标的区间计算统计，不受分页影响；未可比不等于未发生变化。'},
                    'metrics': [{'id': m['id'], 'series': m.get('series'), 'name': m['name'], 'unit': m['unit'], 'freshness': m.get('freshness'), 'quality_issue': m.get('quality_issue'), 'interval_change': c} for m, c in list(zip(rows, changes))[offset:offset+size]],
                    'total': len(rows), 'offset': offset, 'limit': size, 'has_more': offset+size < len(rows),
                    'basis': '每个指标区间内首末有效观测日可能不同；无两点不等于无变化；有效零值不是缺失；不作因果归因。'}, provenance, []
        return await _run_tool(ctx=ctx, tool='geo_metric_changes', handler=handler)

    @server.tool(description="读取数据源菜单的快照来源健康、爬虫项目接入、失败来源及缺发布时间/采集时间计数。冻结快照不是当前实时状态，当前采集另用geo_collector_status。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_source_health(snapshot_id: SnapshotId, offset: Offset = 0, limit: Limit = 20, *, ctx: Context) -> str:
        def handler(principal, _):
            snapshot, provenance = scoped_snapshot(principal, 'geo_source_health', snapshot_id)
            if offset < 0:
                raise MCPError('GEO_INVALID_OFFSET', 'offset不能为负数')
            size = clamp_limit(limit)
            projects, sources = snapshot.get('projects', []), snapshot.get('sources', [])
            from backend.chinese import source_name, translation_cache
            cache = translation_cache()
            return {'projects': [{'name': project_labels().get(p['id'], '新增数据项目'), **{k: p.get(k) for k in ('id', 'state', 'records', 'topic_records', 'failed_sources', 'sources', 'missing_published', 'missing_fetched')}} for p in projects[offset:offset+size]],
                    'sources': [{**{k: s.get(k) for k in ('id', 'monitor', 'state', 'count', 'latest', 'checked_at', 'tier', 'http_status')}, 'error_hint': _sanitize_runtime_text(s.get('error')), 'name': source_name(s.get('name', ''), cache)} for s in sources[offset:offset+size]],
                    'field_semantics': {'project_state': '项目接入状态，不代表所有来源成功', 'source_state': '来源在该快照归档时的检查状态，时点见checked_at', 'missing_published': '项目记录中缺发布时间的条数，不等于缺正文', 'missing_fetched': '项目记录中缺采集时间的条数，不能据此判断从未采集', 'coverage': '全框架候选覆盖，不代表完整性'},
                    'total_projects': len(projects), 'total_sources': len(sources), 'offset': offset, 'limit': size,
                    'has_more': offset+size < max(len(projects), len(sources)),
                    'coverage': research.coverage(snapshot),
                    'basis': '全快照项目/来源统计，不随当前主题筛选。项目可能重复采集同一报道，数量不可相加；维度是候选覆盖；来源失败对维度影响未建立逐条映射，不能臆断缺失范围。'}, provenance, []
        return await _run_tool(ctx=ctx, tool='geo_source_health', handler=handler)

    @server.tool(description="发现最近可见采集批次及启动/结束、采集器失败和导入状态，用run_id再读取geo_collector_status。账本不是调度器或进程心跳。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_collector_runs(limit: Limit = 20, *, ctx: Context) -> str:
        def handler(principal, _):
            authorize(principal, tool='geo_collector_runs')
            size = clamp_limit(limit, maximum=MAX_COLLECTOR_EVENTS)
            runs = load_runs(store.data_dir() / 'collection-runs', project_root() / 'collectors' / 'collectors.json', limit=size+1)
            return {'runs': [{k: r.get(k) for k in ('run_id', 'run_status', 'started_at', 'finished_at', 'updated_at', 'collector_count', 'failed_count', 'import_status', 'snapshot_id')} for r in runs[:size]],
                    'limit': size, 'has_more': len(runs)>size,
                    'collection_snapshot_boundary': collection_snapshot_boundary(),
                    'basis': '仅最近可见账本，可按run_id比较已读批次；有界结果不证明无其他运行。'}, {'kind': 'live', 'source': 'collector-monitor', 'live': True}, []
        return await _run_tool(ctx=ctx, tool='geo_collector_runs', handler=handler)

    @server.tool(description="比较两份已归档简报的看板展示正文，返回差异及快照/生成时间。has_more为true时按next_offset_chars继续读取，不用首段概括全文；文本差异不是事实证实，不生成简报。", annotations={"readOnlyHint": True, "destructiveHint": False}, structured_output=False)
    async def geo_brief_compare(before: Annotated[str, Field(description='前一份简报ID，从geo_brief_list获取。')], after: Annotated[str, Field(description='后一份简报ID，从geo_brief_list获取。')], max_bytes: MaxBytes = MAX_BRIEF_BYTES, offset_chars: Annotated[int, Field(ge=0, description='差异文本的字符偏移，首次0；后续使用next_offset_chars，同一对简报保持一致。')] = 0, *, ctx: Context) -> str:
        def handler(principal, _):
            if max_bytes < 1 or max_bytes > MAX_BRIEF_BYTES:
                raise MCPError('GEO_LIMIT_EXCEEDED', 'max_bytes必须为1—30000')
            briefs = []
            for bid in (before, after):
                with store.db() as conn:
                    row = conn.execute('SELECT id,snapshot_id,created_at,mode,markdown FROM briefs WHERE id=?', (bid,)).fetchone()
                if row is None:
                    raise MCPError('GEO_BRIEF_NOT_FOUND', '研究简报不存在', {'brief_id': bid})
                scoped_snapshot(principal, 'geo_brief_compare', row['snapshot_id'])
                briefs.append(dict(row))
            text = compare_brief_markdown(briefs[0]['markdown'], briefs[1]['markdown'], before, after)
            encoded = text.encode('utf8')
            chunk = text[offset_chars:].encode('utf8')[:max_bytes].decode('utf8', errors='ignore')
            if offset_chars < len(text) and not chunk:
                raise MCPError('GEO_LIMIT_EXCEEDED', 'max_bytes不足以容纳下一个完整UTF-8字符，请增大max_bytes')
            next_offset = offset_chars + len(chunk)
            more = next_offset < len(text)
            return {'before': _brief_projection(briefs[0]), 'after': _brief_projection(briefs[1]), 'diff': chunk, 'truncated': offset_chars > 0 or more,
                    'has_more': more, 'next_offset_chars': next_offset if more else None, 'offset_chars': offset_chars,
                    'total_chars': len(text), 'total_bytes': len(encoded), 'returned_bytes': len(chunk.encode('utf8')),
                    'basis': '与研究简报看板相同的中文展示正文差异（来源标签翻译可更新），不是不可变原文差异；判断是否变化须看正文语义及各自材料，不能自动归因。'}, {'kind': 'brief_comparison', 'snapshot_ids': [b['snapshot_id'] for b in briefs]}, []
        return await _run_tool(ctx=ctx, tool='geo_brief_compare', handler=handler)

    return server


def create_app(*, auth_config: AuthConfig | None = None, managed_keys: bool = False, project_transport: bool = False):
    config = auth_config or AuthConfig.from_env()
    server = create_server(auth_config=config, project_transport=project_transport)
    app = server.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, config=config, mcp_path="/mcp", managed_keys=managed_keys)
    return app


app = create_app(managed_keys=True)
