"""Let an allow-listed GeoAgent deployment ask this one to run collection.

The caller only queues work; locking, retries, reports and import stay with
the existing collector service. A request while a batch is running returns
that batch instead of starting another one, and new batches are rate limited
so a remote caller cannot saturate the overseas egress tunnels.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from backend import store
from backend.collector_config import load_config
from backend.collector_monitor import load_runs
from backend.paths import project_root
from scripts.integrate_collectors import load_registry
from .contracts import MCPError
from .scope import authorize

TOOL = "geo_collect_request"
_request_lock = Lock()


def _allowed_principals():
    return {item.strip() for item in os.getenv("GEO_REMOTE_COLLECT_PRINCIPALS", "").split(",") if item.strip()}


def min_interval_sec():
    return max(0, int(os.getenv("GEO_REMOTE_COLLECT_MIN_INTERVAL_SEC", "1800")))


def _runs_root():
    return store.data_dir() / "collection-runs"


def _state_path():
    return _runs_root() / ".remote-collect.json"


def _running_run_id():
    registry = project_root() / "collectors" / "collectors.json"
    for run in load_runs(_runs_root(), registry, limit=5):
        if run.get("run_status") == "running":
            return run["run_id"]
    return None


# Scheduled hourly news batches run in ~13 min and only archive; a caller that
# reused one would wait for a batch that never produces a snapshot.
COLLECT_ONLY_RETRY_SEC = 900


def _reusable(run_id, now):
    """Return the running batch if it will import a snapshot; refuse a collect-only one."""
    try:
        state = json.loads((_runs_root() / run_id / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if state.get("auto_import", True):
        return run_id
    try:
        elapsed = (now - datetime.fromisoformat(state["started_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        elapsed = 0
    retry = max(60, int(COLLECT_ONLY_RETRY_SEC - elapsed))
    raise MCPError("GEO_COLLECTOR_BUSY",
                   f"定时新闻采集进行中（只采集、不导入快照），约 {max(1, round(retry / 60))} 分钟后可触发；已有快照可直接同步",
                   {"run_id": run_id, "retry_after_sec": retry})


def _validate(ids):
    known = {item["id"] for item in load_registry(project_root() / "collectors" / "collectors.json") if item.get("enabled", True)}
    unknown = sorted(set(ids) - known)
    if unknown:
        raise MCPError("GEO_INVALID_ARGUMENT", "采集器不存在或已停用", {"ids": unknown})
    excluded = sorted(set(ids) & set(load_config().get("exclude_ids", [])))
    if excluded:
        raise MCPError("GEO_INVALID_ARGUMENT", "采集器已在调度配置中排除", {"ids": excluded})


def request_collection(principal, ids=(), topic="", *, jobs=None, now=None):
    authorize(principal, tool=TOOL)
    if principal.principal_id not in _allowed_principals():
        raise MCPError("GEO_TOOL_NOT_ALLOWED", "当前凭据无权触发采集", {"tool": TOOL})
    ids = sorted({str(item) for item in ids})
    _validate(ids)
    if jobs is None:
        from backend.collector_jobs import collector_jobs as jobs
    from backend.collector_jobs import CollectorBusyError
    now = now or datetime.now(timezone.utc)
    interval = min_interval_sec()
    with _request_lock:
        running = _running_run_id()
        if running:
            return {"run_id": _reusable(running, now), "request_id": None, "reused": True, "min_interval_sec": interval}
        try:
            state = json.loads(_state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        last = state.get("requested_at")
        if last:
            waited = (now - datetime.fromisoformat(last)).total_seconds()
            if waited < interval:
                raise MCPError("GEO_RATE_LIMITED", "远端采集触发过于频繁",
                               {"retry_after_sec": int(interval - waited), "run_id": state.get("run_id")})
        config = load_config()
        try:
            job = jobs.submit(ids=ids, topic=topic or None, auto_import=True, allow_partial_import=True,
                              exclude_ids=list(config.get("exclude_ids", [])))
        except CollectorBusyError:
            # A scheduled or page-started run took the lock after the scan above.
            running = _running_run_id()
            if running:
                return {"run_id": _reusable(running, now), "request_id": None, "reused": True, "min_interval_sec": interval}
            raise MCPError("GEO_COLLECTOR_BUSY", "采集服务正忙，请稍后重试")
        _runs_root().mkdir(parents=True, exist_ok=True)
        _state_path().write_text(json.dumps({"run_id": job.run_id, "requested_at": now.isoformat(),
                                             "principal_id": principal.principal_id}), encoding="utf-8")
    return {"run_id": job.run_id, "request_id": job.request_id, "reused": False,
            "submitted_at": job.submitted_at, "min_interval_sec": interval}


def register_collect_tools(server, run_tool):
    # The MCP surface is read-only for research agents. Only a deployment that
    # names a trusted caller exposes this one write-capable tool at all.
    if not _allowed_principals():
        return

    @server.tool(name=TOOL, description="请求本服务执行一批采集并在结束后导入快照（部分源失败也入库）。只排队、立即返回run_id，用geo_collector_status(run_id)查询进度，完成后用geo_mirror_manifest拉取快照。已有批次运行时返回该批次（reused=true）；新批次有最短间隔。仅限白名单凭据。", annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}, structured_output=False)
    async def geo_collect_request(
        ctx: Context,
        ids: Annotated[list[str] | None, Field(description="采集器ID列表；空或null为全部启用采集器。")] = None,
        topic: Annotated[str, Field(max_length=40, description="主题机器值，空串为全部。")] = "",
    ) -> str:
        def handler(principal, _):
            result = request_collection(principal, ids or (), topic)
            return result, {"kind": "collector-request", "run_id": result["run_id"]}, []
        return await run_tool(ctx=ctx, tool=TOOL, handler=handler)
