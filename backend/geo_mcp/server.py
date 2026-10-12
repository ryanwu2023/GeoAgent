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
from typing import Any, Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend import research, store
from backend.catalog import TOPICS, RULE_VERSION, topic_dimensions
from backend.events import SIGNALS, event_metadata
from backend.collector_monitor import load_ledger_events, load_runs
from backend.paths import project_root
from scripts.integrate_collectors import load_registry

from .audit import record, request_id_from_context
from .auth import AuthConfig, BearerAuthMiddleware, principal_from_request
from .contracts import MCPError, MAX_TEXT_BYTES, clamp_limit, success_envelope
from .provenance import list_provenance, snapshot_provenance
from .scope import authorize


logger = logging.getLogger("geoagent.mcp")

MAX_BRIEF_BYTES = 30_000
MAX_COLLECTOR_EVENTS = 100


def _topic_ids() -> set[str]:
    return {topic["id"] for topic in TOPICS}


def _validate_filters(topic: str, dimension: str) -> None:
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
    reviews = {row["record_id"]: row["signal"] for row in rows}
    latest_reviews = {row["record_id"]: row for row in rows}
    applied_reviews = [latest_reviews[item["id"]] for item in records if item["id"] in latest_reviews]
    counts = {signal: 0 for signal in SIGNALS}
    for item in records:
        signal = reviews.get(item["id"])
        if signal is None:
            signal = event_metadata(item)["situation_signal"]
        counts[signal if signal in counts else "unknown"] += 1
    coverage = research.coverage(snapshot, start=start, end=end)
    dimensions = [item for row in coverage for item in row["dimensions"]]
    return {
        "analysis_evidence_boundary": _analysis_evidence_boundary(),
        "records_count": len(records),
        "supplemental_records_count": sum(bool(item.get("supplemental")) for item in records),
        "signal_counts": counts,
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
    result["text"] = (record_item.get("text") or "")[:MAX_TEXT_BYTES]
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
                "source_health",
                "error_type",
            )
            if key in item
        }
        if "error_message" in item:
            projected["error_message"] = _sanitize_runtime_text(item.get("error_message"), limit=1000)
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


def _run_tool(
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
        return success_envelope(
            data=data,
            request_id=request_id,
            tool=tool,
            provenance=provenance,
            warnings=warnings,
            audit_event_id=audit_event_id,
        )
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

    @server.tool(name="geo_snapshot_list", description="列出当前 principal 可见的 GeoAgent 快照版本和统计。", structured_output=False)
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

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_snapshot_get", description="读取一个可见快照的轻量元数据，不返回全量 records。", structured_output=False)
    async def geo_snapshot_get(snapshot_id: str, ctx: Context) -> str:
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
            data = {"snapshot": summary}
            return data, snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_id(summaries)), []

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_workspace_summary", description="读取风险监测面板：升级相关/持续相关/缓和相关/待研判报道数量 signal_counts、范围内记录数 records_count、全框架候选维度覆盖，以及指标、资产和情景摘要。问‘风险概览、各类线索多少、维度覆盖多少、还缺什么数据’时使用。review_evidence 仅证明本次应用的复核，change_attribution 限定差异原因尚未证实，不能把可更新机制当成实际变更原因。报道计数不是 scenarios.evidence_ids 的有限候选样本数量，也不是情景概率。coverage 排除补充报告且为全部主题，coverage[].count 是主题记录数而非维度合计；records_count 遵循 topic/dimension/日期筛选。", structured_output=False)
    async def geo_workspace_summary(
        snapshot_id: str,
        topic: str = "all",
        dimension: str = "",
        start: str = "",
        end: str = "",
        limit: int = 20,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_workspace_summary"
        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            _validate_filters(topic, dimension)
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
                "assets": analysis.get("assets", []),
                "scenarios": analysis.get("scenarios", []),
                "observations_count": len(metrics),
            }
            warnings = analysis.get("data_warnings", [])
            return summary, snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_id(store.snapshot_summaries())), warnings

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_metric_catalog", description="列出快照内可用指标的轻量目录，不返回完整时间序列。", structured_output=False)
    async def geo_metric_catalog(
        snapshot_id: str,
        topic: str = "all",
        dimension: str = "",
        start: str = "",
        end: str = "",
        offset: int = 0,
        limit: int = 20,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_metric_catalog"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            _validate_filters(topic, dimension)
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
                snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_id(store.snapshot_summaries())),
                [],
            )

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_metric_series", description="读取快照内指定指标的有界时间序列和程序计算摘要。", structured_output=False)
    async def geo_metric_series(
        snapshot_id: str,
        series: str,
        topic: str = "all",
        dimension: str = "",
        start: str = "",
        end: str = "",
        max_points: int = 50,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_metric_series"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            _validate_filters(topic, dimension)
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
                snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_id(store.snapshot_summaries())),
                warnings,
            )

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_evidence_search", description="在指定快照内分页检索证据摘要。", structured_output=False)
    async def geo_evidence_search(
        snapshot_id: str,
        q: str = "",
        topic: str = "all",
        dimension: str = "",
        start: str = "",
        end: str = "",
        offset: int = 0,
        limit: int = 20,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_evidence_search"
        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            _validate_filters(topic, dimension)
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
            matched = _search_records(records, q)
            page = matched[offset : offset + page_limit]
            return (
                {"analysis_evidence_boundary": _analysis_evidence_boundary(), "records": [_record_summary(item) for item in page], "offset": offset, "limit": page_limit, "has_more": offset + page_limit < len(matched), "total": len(matched)},
                snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_id(store.snapshot_summaries())),
                [],
            )

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_evidence_get", description="读取指定快照内单条证据的正文、来源、发布/采集时间与核验状态。报道及单方通报不是独立核实事实；单条或有限样本不能证明整个快照缺少某类证据，见 analysis_evidence_boundary。", structured_output=False)
    async def geo_evidence_get(snapshot_id: str, record_id: str, ctx: Context) -> str:
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
            return {"analysis_evidence_boundary": _analysis_evidence_boundary(), "record": _redacted_record(item)}, snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_id(store.snapshot_summaries())), []

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_analysis_view", description="读取快照内五类资产和三类情景的结构化条件研判。对照业务面板时须保持同一 topic、dimension、start、end；用户未指定日期或维度时不要自行收窄。不同筛选的 topic_paths/status 不可拼接成同一面板结论。asset_overview 是精简五类资产信息，完整 assets 含更多材料。", structured_output=False)
    async def geo_analysis_view(
        snapshot_id: str,
        topic: str = "all",
        dimension: str = "",
        start: str = "",
        end: str = "",
        limit: int = 20,
        *,
        ctx: Context,
    ) -> str:
        tool = "geo_analysis_view"

        def handler(principal: Any, _: str) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
            _validate_filters(topic, dimension)
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
                "important_metrics": (result.get("important_metrics") or [])[:page_limit],
                "total_assets": len(result.get("assets") or []),
                "total_scenarios": len(result.get("scenarios") or []),
            }
            return (
                data,
                snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_id(store.snapshot_summaries())),
                result.get("data_warnings") or [],
            )

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_brief_list", description="列出 principal 可见的已落盘研究简报元数据。", structured_output=False)
    async def geo_brief_list(
        snapshot_id: str = "",
        offset: int = 0,
        limit: int = 20,
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

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_brief_get", description="读取已落盘研究简报的受限正文和 snapshot lineage。", structured_output=False)
    async def geo_brief_get(brief_id: str, max_bytes: int = MAX_BRIEF_BYTES, *, ctx: Context) -> str:
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
            encoded = row["markdown"].encode("utf-8")
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
                    }
                },
                snapshot_provenance(snapshot, working_snapshot_id=working_id, latest_snapshot_id=_latest_id(store.snapshot_summaries())),
                warnings,
            )

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    @server.tool(name="geo_collector_status", description="读取采集运行态的有界 live 投影，不混入快照 provenance。", structured_output=False)
    async def geo_collector_status(
        run_id: str = "",
        collector_id: str = "",
        limit: int = 20,
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
                runs = load_runs(runs_root, registry_path, limit=max(page_limit, 20))
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

        return _run_tool(ctx=ctx, tool=tool, handler=handler)

    return server


def create_app(*, auth_config: AuthConfig | None = None, managed_keys: bool = False, project_transport: bool = False):
    config = auth_config or AuthConfig.from_env()
    server = create_server(auth_config=config, project_transport=project_transport)
    app = server.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, config=config, mcp_path="/mcp", managed_keys=managed_keys)
    return app


app = create_app(managed_keys=True)
