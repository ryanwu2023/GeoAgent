from . import llm
from starlette.middleware.gzip import GZipMiddleware
from .link_health import link_health, summary as link_summary
import json
import os
import httpx
from urllib.parse import urlsplit
from pathlib import Path
from typing import Literal
from datetime import date
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .store import ROOT, snapshots, snapshot_summaries, get_snapshot, save_snapshot, db, data_dir, digest, get_working_snapshot_id, set_working_snapshot_id
from .adapters import load_bundle
from .research import filtered_records, metrics, analysis, coverage, answer, create_brief, model_status, page_summary
from .semantic import risk_panel_counts
from .catalog import RULE_VERSION, topic_dimensions
from .chinese import present_record, present_record_summary, present_map_record, present_answer, translation_cache, source_name, start_translation, translation_status, localize_brief_markdown
from .paths import collector_read_root, collector_data_root, sync_local_collector_outputs
from .collector_monitor import load_latest_run, load_runs, load_ledger_events, load_ledger_runs, load_source_status
from .collector_jobs import CollectorBusyError, collector_jobs, recover_interrupted_runs
from .collector_config import apply_config, load_config, save_config
from .collector_import import import_collector_output
from .collector_ledger import CollectorLedger
from scripts.integrate_collectors import load_registry
from .mcp_news import McpClient, load_config as load_mcp_config, public_config as public_mcp_config, save_config as save_mcp_config, store_news
from .external_mcp import public_registry as public_external_mcp_registry, test_and_save as test_and_save_external_mcp, delete_server as delete_external_mcp_server, news_import_capability, _safe_error as safe_mcp_error
from .brief_templates import list_templates, get_template, save_template, set_default as set_default_brief_template, delete_template as delete_brief_template

def load_env():
    file = ROOT / ".env"
    if file.exists():
        for line in file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if not key.strip().startswith(('LLM_', 'PICHAT_')):
                    os.environ.setdefault(key.strip(), value.strip().strip('"'))

load_env()
from .integrations import router as integrations_router, settings as integration_settings, pichat_tls_context
from .geo_mcp.server import create_app as create_mcp_app
from .geo_mcp.auth import AuthConfig, BearerAuthMiddleware
from starlette.routing import Route

mcp_config = AuthConfig.from_env()

class ProjectMCP:
    async def __call__(self, scope, receive, send):
        await app.state.geo_mcp(scope, receive, send)

@asynccontextmanager
async def lifespan(app):
    recover_interrupted_runs()
    start_translation()
    mcp_app = create_mcp_app(auth_config=mcp_config, managed_keys=True, project_transport=True)
    app.state.geo_mcp = mcp_app
    async with mcp_app.router.lifespan_context(mcp_app):
        yield

app = FastAPI(title="地缘政治研究接口", version="0.6.1",lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(BearerAuthMiddleware, config=mcp_config, managed_keys=True)
app.include_router(integrations_router)
from .model_routes import router as model_router, test_translation
app.include_router(model_router)
from .mcp_workbench import router as workbench_router
app.include_router(workbench_router)
# Delegate at the native path, with a fresh SDK session manager per lifecycle.
app.router.routes.append(Route("/mcp", endpoint=ProjectMCP()))

@app.get("/healthz")
def mcp_health():
    return {"status": "ok", "service": "geoagent-mcp", "protocol_version": "geo-mcp/v1"}
Topic = Literal["all", "usiran", "ukraine"]


@app.get("/api/pichat/embed-identity")
async def pichat_embed_identity(agent_id: str = "geo-research-assistant"):
    """Mint the short-lived PiChat panel identity for the local GeoAgent UI.

    The pk- application key and product phone stay server-side. The browser only
    receives the short-lived panel JWT needed by the iframe handshake.
    """
    cfg = integration_settings()
    base_url = cfg["origin"].rstrip("/")
    app_key = cfg["app_key"]
    phone = cfg["phone"]
    agent_id = cfg["agent_id"]
    if not app_key or not phone:
        raise HTTPException(
            status_code=503,
            detail="未配置 PICHAT_EMBED_APP_KEY / PICHAT_EMBED_PHONE；密钥只能由 GeoAgent 后端读取。",
        )
    entry = cfg["entry"]
    payload = {
        "phone": phone,
        "productUserId": cfg["product_user_id"],
        "displayName": "GeoAgent 本地研究用户",
        "clientId": "geoagent-ui",
        "expiresInSec": 3600,
    }
    payload["entry" if entry else "agentId"] = entry or agent_id
    try:
        async with httpx.AsyncClient(timeout=10, verify=pichat_tls_context()) as client:
            response = await client.post(
                f"{base_url}/api/v1/embed/identity",
                headers={"Authorization": f"Bearer {app_key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"PiChat 身份服务不可用: {exc}") from exc
    if response.status_code in (401, 403):
        # Credential/policy failures are not transient transport failures.
        # Never forward the upstream body (which can contain private details).
        code = "PICHAT_APP_KEY_REJECTED" if response.status_code == 401 else "PICHAT_EMBED_FORBIDDEN"
        raise HTTPException(status_code=502, detail={"code": code, "upstream_status": response.status_code})
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"PiChat 身份签发失败: HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="PiChat 身份服务未返回 JSON，请检查服务根地址") from exc
    if not body.get("ok") or not body.get("data", {}).get("token"):
        raise HTTPException(status_code=502, detail="PiChat 未返回有效的嵌入身份")
    return {"agentId": body["data"].get("agentId", agent_id), **body["data"]}

class CollectorRunRequest(BaseModel):
    # Bound request size independently of registry growth; the route still
    # rejects unknown/disabled collector IDs before queueing any work.
    ids: list[str] = Field(default_factory=list, max_length=100)
    topic: str | None = Field(default=None, max_length=80)
    timeout: int = Field(default=900, ge=1, le=3600)
    retry_attempts: int = Field(default=2, ge=1, le=5)
    retry_backoff_sec: float = Field(default=1.0, ge=0, le=60)
    auto_import: bool = True
    allow_partial_import: bool | None = None


class CollectorConfigRequest(BaseModel):
    interval_sec: int = Field(default=86400, ge=60, le=604800)
    exclude_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_partial_import: bool = False
    apply: bool = False


class WorkspaceSelectionRequest(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=120)


class PageSummaryRequest(BaseModel):
    snapshot_id: str = Field(min_length=8, max_length=128)
    page: Literal["metrics", "scenarios", "assets"]
    topic: Topic = "all"
    dimension: str = Field(default="", max_length=120)
    start: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    end: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")


class CollectorReviewRequest(BaseModel):
    status: Literal["pending", "accepted", "rejected"]
    reviewer: str = Field(default="本地用户", min_length=1, max_length=80)
    reason: str = Field(default="", max_length=1500)
    evidence: dict | list | None = None


class CollectorGateRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewer: str = Field(default="本地用户", min_length=1, max_length=80)
    reason: str = Field(default="", max_length=1500)


class CollectorImportRetryRequest(BaseModel):
    allow_partial_import: bool = False


REVIEW_ITEMS = (
    ("sources", "来源健康"),
    ("candidates", "候选加工"),
    ("gate", "发布门禁"),
    ("import", "清洗与统一导入"),
    ("snapshot", "快照范围"),
)

@app.middleware("http")
async def protect_local_mutations(request: Request, call_next):
    # Explicit origins only; do not trust caller-controlled Host headers.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        local_origins = {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (8000, 8010, 8011, 5173)}
        local_origins.update(value.strip() for value in os.getenv("GEOAGENT_ALLOWED_ORIGINS", "").split(",") if value.strip())
        if origin and origin not in local_origins:
            return PlainTextResponse("不允许来自其他站点的写入请求", status_code=403)
    response = await call_next(request)
    if request.url.path == "/api/pichat/embed-identity":
        response.headers["Cache-Control"] = "no-store"
    return response

def snapshot_or_404(sid):
    try:
        return get_snapshot(sid)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def working_snapshot_or_409(sid):
    """Require downstream analysis to use the explicitly confirmed snapshot."""
    selected = get_working_snapshot_id()
    if not selected:
        raise HTTPException(409, "尚未确认工作快照，请先在快照版本谱系中设为工作快照")
    if selected != sid:
        raise HTTPException(409, "请求快照不是当前工作快照，请先确认或切换工作快照")
    return snapshot_or_404(sid)

def validate_range(start, end):
    try:
        for value in (start, end):
            if value:
                date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, "日期无效，请使用 YYYY-MM-DD") from exc
    if start and end and start > end:
        raise HTTPException(422, "起始日期不能晚于结束日期")

def legacy_crawler_status():
    crawler = {"state": "idle", "total": 0, "current": None, "results": []}
    crawler_file = data_dir() / "crawler-runs" / "current.json"
    if crawler_file.is_file():
        try:
            crawler = {**crawler, **json.loads(crawler_file.read_text(encoding="utf-8"))}
        except (OSError, ValueError, TypeError):
            crawler = {**crawler, "state": "status_error"}
    return crawler

@app.get("/api/status")
def status(snapshot_id: str | None = None):
    if snapshot_id:
        snapshot_or_404(snapshot_id)
    return {"model": model_status(), "translation":translation_status(snapshot_id), "crawler_root": str(collector_read_root()), "crawler": legacy_crawler_status(), "mcp": public_mcp_config(), "rule_version": RULE_VERSION, "snapshots": snapshots(), "working_snapshot_id": get_working_snapshot_id()}

@app.get("/api/collector/snapshots")
def collector_snapshots():
    """Expose snapshot version statistics without returning the full payload."""
    return {"snapshots": snapshot_summaries(), "source": "research-sqlite-snapshot-projection"}

@app.get("/api/workspace-selection")
def workspace_selection():
    snapshot_id = get_working_snapshot_id()
    return {"snapshot_id": snapshot_id, "available": bool(snapshot_id), "source": "research-sqlite-workspace-state"}

@app.put("/api/workspace-selection")
def update_workspace_selection(request: WorkspaceSelectionRequest):
    try:
        snapshot_id = set_working_snapshot_id(request.snapshot_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"snapshot_id": snapshot_id, "available": True, "source": "research-sqlite-workspace-state"}

@app.get("/api/collector/summary")
def collector_summary():
    """Expose the newest persisted collection report without claiming live health."""
    return load_latest_run(data_dir() / "collection-runs", ROOT / "collectors" / "collectors.json")


@app.get("/api/collector/config")
def collector_config():
    registry = load_registry(ROOT / "collectors" / "collectors.json")
    config = load_config()
    excluded = set(config.get("exclude_ids", []))
    return {
        "config": config,
        "scope": "service_interval",
        "description": "当前服务按全局间隔运行 enabled 采集器；采集成功后自动导入快照，简报需要单独生成。",
        "collectors": [
            {
                "id": item["id"],
                "name": item.get("name_zh", item["id"]),
                "enabled": item.get("enabled", True),
                "excluded": item["id"] in excluded,
            }
            for item in registry
        ],
    }


@app.put("/api/collector/config")
def update_collector_config(request: CollectorConfigRequest):
    registry = load_registry(ROOT / "collectors" / "collectors.json")
    # Keep disabled registry entries valid in a previously saved exclusion
    # list; the service itself still only runs enabled collectors.
    known = {item["id"] for item in registry}
    try:
        config = save_config(
            {
                "interval_sec": request.interval_sec,
                "exclude_ids": request.exclude_ids,
                "auto_import": True,
                "allow_partial_import": request.allow_partial_import,
            },
            known_ids=known,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    applied = {"applied": False, "message": "已保存项目配置；尚未重载后台任务"}
    if request.apply:
        applied = apply_config(config)
    return {"config": config, **applied}

@app.get("/api/collector/runs")
def collector_runs(limit: int = Query(20, ge=1, le=100)):
    """List persisted reports; this is history, not a live scheduler heartbeat."""
    return {
        "runs": load_runs(data_dir() / "collection-runs", ROOT / "collectors" / "collectors.json", limit),
        "source": "collection-run-report",
    }

@app.get("/api/collector/ledger")
def collector_ledger(limit: int = Query(20, ge=1, le=100)):
    """Expose scheduler lifecycle rows when the service has created a ledger."""
    return load_ledger_runs(data_dir() / "collection-runs", limit)

@app.get("/api/collector/sources")
def collector_sources(
    collector_id: str | None = Query(None, max_length=120),
    run_id: str | None = Query(None, max_length=120),
):
    """Expose source-level artifacts without probing the network from the API."""
    collector_ids = None
    if run_id:
        run = next(
            (
                item
                for item in load_runs(data_dir() / "collection-runs", ROOT / "collectors" / "collectors.json", 100)
                if item["run_id"] == run_id
            ),
            None,
        )
        if run is None:
            return {
                "available": False,
                "source": "run-not-found",
                "collector_id": collector_id,
                "run_id": run_id,
                "scope": "run-collector-source-artifact",
                "collectors": [],
            }
        collector_ids = {item["id"] for item in run.get("collectors", [])}
        if collector_id:
            collector_ids &= {collector_id}
    return load_source_status(
        collector_data_root(),
        collector_id=collector_id,
        collector_ids=collector_ids,
        run_id=run_id,
    )


@app.get("/api/collector/source-records")
def collector_source_records(
    collector_id: str = Query(..., min_length=1, max_length=120),
    source_id: str = Query(..., min_length=1, max_length=160),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    """Read a bounded preview of records for one globally published source.

    This is deliberately read-only and uses the persistent collector output
    tree, not the currently selected run. It never triggers collection or
    snapshot import.
    """
    if Path(collector_id).name != collector_id or Path(source_id).name != source_id:
        raise HTTPException(400, "非法来源标识")
    output = collector_data_root() / collector_id / "output"
    record_file = output / "ALL-records.jsonl"
    if not record_file.is_file():
        record_file = output / "LATEST-records.jsonl"
    if not record_file.is_file():
        return {"available": False, "collector_id": collector_id, "source_id": source_id, "total": 0, "records": []}
    matches = []
    try:
        with record_file.open("r", encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and str(record.get("source_id", "")) == source_id:
                    matches.append(record)
    except OSError as exc:
        raise HTTPException(500, f"来源记录读取失败：{exc}") from exc
    return {
        "available": True,
        "collector_id": collector_id,
        "source_id": source_id,
        "total": len(matches),
        "offset": offset,
        "limit": limit,
        "records": matches[offset:offset + limit],
    }


def _find_collector_run(run_id: str) -> dict | None:
    return next(
        (
            item
            for item in load_runs(data_dir() / "collection-runs", ROOT / "collectors" / "collectors.json", 100)
            if item["run_id"] == run_id
        ),
        None,
    )


def _collector_ledger() -> CollectorLedger:
    return CollectorLedger(data_dir() / "collection-runs" / "ledger.sqlite3")


def _review_projection(run_id: str) -> dict:
    ledger_path = data_dir() / "collection-runs" / "ledger.sqlite3"
    saved = {}
    if ledger_path.is_file():
        saved = {item["item_id"]: item for item in CollectorLedger(ledger_path).reviews(run_id)}
    items = []
    for item_id, label in REVIEW_ITEMS:
        items.append({"item_id": item_id, "label": label, **saved.get(item_id, {"status": "pending", "reviewer": None, "reason": "", "updated_at": None})})
    return {"available": ledger_path.is_file(), "source": "collector-ledger-review", "run_id": run_id, "items": items}


@app.get("/api/collector/runs/{run_id}/review")
def collector_run_review(run_id: str):
    if _find_collector_run(run_id) is None:
        raise HTTPException(404, "采集运行记录不存在")
    return _review_projection(run_id)


@app.put("/api/collector/runs/{run_id}/review/{item_id}")
def update_collector_review(run_id: str, item_id: str, request: CollectorReviewRequest):
    if _find_collector_run(run_id) is None:
        raise HTTPException(404, "采集运行记录不存在")
    if item_id not in {item[0] for item in REVIEW_ITEMS}:
        raise HTTPException(404, "核对项不存在")
    ledger = _collector_ledger()
    run = ledger.run(run_id)
    if run is None:
        projected = _find_collector_run(run_id)
        ledger.ensure_run(run_id=run_id, started_at=projected.get("started_at") if projected else None)
    try:
        review = ledger.upsert_review(
            run_id=run_id,
            item_id=item_id,
            status=request.status,
            reviewer=request.reviewer.strip(),
            reason=request.reason.strip(),
            evidence=request.evidence,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    ledger.event(run_id=run_id, event_type="review_updated", status=request.status, message=f"人工核对：{item_id}", payload=review)
    return {"available": True, "source": "collector-ledger-review", "run_id": run_id, "review": review}


@app.post("/api/collector/runs/{run_id}/gate")
def decide_collector_gate(run_id: str, request: CollectorGateRequest):
    projected = _find_collector_run(run_id)
    if projected is None:
        raise HTTPException(404, "采集运行记录不存在")
    ledger = _collector_ledger()
    if ledger.run(run_id) is None:
        ledger.ensure_run(run_id=run_id, started_at=projected.get("started_at"))
    ledger.update_gate(run_id=run_id, decision=request.decision, reviewer=request.reviewer.strip(), reason=request.reason.strip())
    status = "accepted" if request.decision == "approved" else "rejected"
    review = ledger.upsert_review(run_id=run_id, item_id="gate", status=status, reviewer=request.reviewer.strip(), reason=request.reason.strip())
    ledger.event(run_id=run_id, event_type="gate_decision", status=request.decision, message=request.reason.strip() or "人工发布门禁决策", payload={"review": review})
    return {"available": True, "source": "collector-ledger-review", "run_id": run_id, "decision": request.decision, "review": review}


def _load_report_payload(projected: dict) -> tuple[Path, dict]:
    report_path = Path(projected["report_path"])
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(422, f"运行报告无法读取：{type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(422, "运行报告格式无效")
    return report_path, payload


@app.get("/api/collector/runs/{run_id}/candidate-audit")
def collector_candidate_audit(run_id: str):
    projected = _find_collector_run(run_id)
    if projected is None:
        raise HTTPException(404, "采集运行记录不存在")
    _, payload = _load_report_payload(projected)
    candidate_metrics = payload.get("candidate_metrics") if isinstance(payload.get("candidate_metrics"), dict) else None
    cleaning = (payload.get("import_result") or {}).get("cleaning") if isinstance(payload.get("import_result"), dict) else None
    return {
        "available": bool(candidate_metrics or cleaning),
        "source": "collector-run-report",
        "run_id": run_id,
        "metrics": candidate_metrics or cleaning or {},
        "reason": None if candidate_metrics or cleaning else "本批次运行报告没有持久化候选分层或导入清洗统计",
    }


@app.get("/api/collector/runs/{run_id}/import-detail")
def collector_import_detail(run_id: str):
    projected = _find_collector_run(run_id)
    if projected is None:
        raise HTTPException(404, "采集运行记录不存在")
    return {
        "available": True,
        "source": "collector-run-report",
        "run_id": run_id,
        "run_status": projected.get("run_status"),
        "import_status": projected.get("import_status"),
        "snapshot_id": projected.get("snapshot_id"),
        "import_result": projected.get("import_result"),
        "import_error": projected.get("import_error"),
    }


@app.post("/api/collector/runs/{run_id}/import-retry")
def retry_collector_import(run_id: str, request: CollectorImportRetryRequest):
    projected = _find_collector_run(run_id)
    if projected is None:
        raise HTTPException(404, "采集运行记录不存在")
    if projected.get("import_status") == "succeeded":
        return {"available": True, "source": "collector-import", "run_id": run_id, "import_status": "succeeded", "snapshot_id": projected.get("snapshot_id"), "reused": True}
    run_status = projected.get("run_status")
    if run_status not in {"success", "partial"}:
        raise HTTPException(409, f"当前运行状态不允许仅重试导入：{run_status or '未知'}")
    ledger = _collector_ledger()
    ledger_run = ledger.run(run_id)
    if run_status == "partial":
        if not request.allow_partial_import:
            raise HTTPException(409, "本批次部分成功，需勾选允许部分成功并先批准发布门禁")
        if not ledger_run or ledger_run.get("gate_decision") != "approved":
            raise HTTPException(409, "部分成功导入需要先在发布门禁中记录批准")
    report_path, payload = _load_report_payload(projected)
    try:
        result = import_collector_output()
    except Exception as exc:
        message = f"{type(exc).__name__}: {str(exc)[:1000]}"
        payload.update({"import_status": "failed", "import_error_type": type(exc).__name__, "import_error_message": message})
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        state_path = report_path.parent / "state.json"
        if state_path.exists():
            state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if ledger.run(run_id) is None:
            ledger.ensure_run(run_id=run_id, started_at=projected.get("started_at"))
        ledger.update_import(run_id=run_id, import_status="failed", import_error_type=type(exc).__name__, import_error_message=message)
        ledger.event(run_id=run_id, event_type="import_failed", status="failed", message=message, payload={"error_type": type(exc).__name__})
        raise HTTPException(422, f"仅重试导入失败：{message}") from exc
    payload.update({"import_status": "succeeded", "snapshot_id": result.get("snapshot_id"), "import_result": result, "import_error_type": None, "import_error_message": None})
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    state_path = report_path.parent / "state.json"
    if state_path.exists():
        state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ledger = _collector_ledger()
    if ledger.run(run_id) is None:
        ledger.ensure_run(run_id=run_id, started_at=projected.get("started_at"))
    ledger.update_import(run_id=run_id, import_status="succeeded", snapshot_id=result.get("snapshot_id"))
    ledger.event(run_id=run_id, event_type="import_retry_finished", status="succeeded", payload=result)
    return {"available": True, "source": "collector-import", "run_id": run_id, "import_status": "succeeded", **result}

@app.post("/api/collector/runs", status_code=202)
def start_collector_run(request: CollectorRunRequest):
    """Queue a manual run; the collector service still owns execution and locks."""
    registry = load_registry(ROOT / "collectors" / "collectors.json")
    known = {item["id"] for item in registry if item.get("enabled", True)}
    unknown = sorted(set(request.ids) - known)
    if unknown:
        raise HTTPException(422, f"采集器不存在或已停用：{', '.join(unknown)}")
    config = load_config()
    configured_exclude_ids = sorted(set(config.get("exclude_ids", [])))
    explicitly_excluded = sorted(set(request.ids) & set(configured_exclude_ids))
    if explicitly_excluded:
        raise HTTPException(409, f"采集器已在调度配置中排除，不能手动执行：{', '.join(explicitly_excluded)}")
    configured_allow_partial = bool(config.get("allow_partial_import", False))
    try:
        job = collector_jobs.submit(
            ids=request.ids,
            topic=request.topic,
            timeout=request.timeout,
            retry_attempts=request.retry_attempts,
            retry_backoff_sec=request.retry_backoff_sec,
            auto_import=request.auto_import,
            allow_partial_import=configured_allow_partial if request.allow_partial_import is None else request.allow_partial_import,
            exclude_ids=configured_exclude_ids,
        )
    except CollectorBusyError as exc:
        raise HTTPException(409, f"已有采集服务运行中：{str(exc)}") from exc
    return {**job.public(), "source": "collector-job"}

@app.post("/api/collector/runs/{run_id}/retry", status_code=202)
def retry_collector_run(run_id: str):
    """Queue only failed collectors from a persisted report."""
    run = next((item for item in load_runs(data_dir() / "collection-runs", ROOT / "collectors" / "collectors.json", 100) if item["run_id"] == run_id), None)
    if run is None:
        raise HTTPException(404, "采集运行记录不存在")
    failed_ids = [item["id"] for item in run.get("collectors", []) if item.get("status") == "failed"]
    if not failed_ids:
        raise HTTPException(422, "该运行没有可重试的失败采集器")
    try:
        config = load_config()
        allowed_failed_ids = [item for item in failed_ids if item not in set(config.get("exclude_ids", []))]
        if not allowed_failed_ids:
            raise HTTPException(409, "该批次失败项都已被当前调度配置排除，无需重试")
        job = collector_jobs.submit(
            ids=allowed_failed_ids,
            auto_import=True,
            allow_partial_import=bool(config.get("allow_partial_import", False)),
            exclude_ids=list(config.get("exclude_ids", [])),
        )
    except CollectorBusyError as exc:
        raise HTTPException(409, f"已有采集服务运行中：{str(exc)}") from exc
    return {**job.public(), "source": "collector-job", "retry_of": run_id}

@app.get("/api/collector/jobs/{request_id}")
def collector_job(request_id: str):
    job = collector_jobs.get(request_id)
    if job is None:
        raise HTTPException(404, "采集请求不存在")
    return {**job.public(), "source": "collector-job"}

@app.get("/api/collector/runs/{run_id}/events")
def collector_run_events(
    run_id: str,
    collector_id: str | None = Query(None, max_length=120),
    limit: int = Query(100, ge=1, le=500),
):
    """Expose read-only run events for the monitor detail panel."""
    return load_ledger_events(
        data_dir() / "collection-runs",
        run_id,
        collector_id=collector_id,
        limit=limit,
    )

@app.get("/api/collector/runs/{run_id}/logs/{collector_id}")
def collector_run_log(
    run_id: str,
    collector_id: str,
    stream: Literal["stdout", "stderr"] = "stderr",
    tail: int = Query(2000, ge=1, le=10000),
):
    """Return a bounded, redacted output tail for a collector item."""
    from .collector_logs import read_log_tail

    try:
        return read_log_tail(data_dir() / "collection-runs", run_id, collector_id, stream=stream, tail=tail)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.get("/api/collector/runs/{run_id}")
def collector_run(run_id: str):
    for run in load_runs(data_dir() / "collection-runs", ROOT / "collectors" / "collectors.json", 100):
        if run["run_id"] == run_id:
            return run
    raise HTTPException(404, "采集运行记录不存在")


class McpConfigInput(BaseModel):
    server_id: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=1000)
    authorization: str = Field(default="", max_length=4000)
    verify_tls: bool = True


class ExternalMcpInput(BaseModel):
    id: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=80)
    type: Literal["http"] = "http"
    url: str = Field(min_length=8, max_length=1000)
    key: str = Field(default="", max_length=4000)
    verify_tls: bool | None = None


class McpTestInput(BaseModel):
    server_id: str | None = Field(default=None, max_length=100)


class McpFetchInput(BaseModel):
    server_id: str | None = Field(default=None, max_length=100)
    query: str = Field(min_length=2, max_length=500)
    topic: Literal["usiran", "ukraine"]
    dimension: str = Field(min_length=1, max_length=80)
    top_k: int = Field(default=20, ge=1, le=100)
    date_from: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    date_to: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")


class TranslationBatchInput(BaseModel):
    snapshot_id: str = Field(min_length=8, max_length=128)
    record_ids: list[str] = Field(min_length=1, max_length=12)


@app.get("/api/mcp/config")
def get_mcp_news_config(server_id: str | None = None):
    try:
        return public_mcp_config(server_id=server_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/integrations/external-mcp")
def get_external_mcp_servers():
    try:
        return public_external_mcp_registry()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/integrations/external-mcp/test-and-save")
async def save_external_mcp_server(body: ExternalMcpInput):
    try:
        return await test_and_save_external_mcp(body.id, body.name, body.type, body.url, body.key, body.verify_tls)
    except (ValueError, OSError, httpx.HTTPError, json.JSONDecodeError) as exc:
        raise HTTPException(422, f"MCP 连通性测试失败：{str(exc)[:300]}") from exc


@app.delete("/api/integrations/external-mcp/{server_id}")
def remove_external_mcp_server(server_id: str):
    try:
        return delete_external_mcp_server(server_id)
    except KeyError as exc:
        raise HTTPException(404, "MCP 服务不存在") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/mcp/config")
def update_mcp_config(body: McpConfigInput):
    try:
        return save_mcp_config(body.name, body.url, body.authorization, body.verify_tls, body.server_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/mcp/test")
async def test_mcp(body: McpTestInput | None = None):
    config = {}
    try:
        config = load_mcp_config(body.server_id if body else None)
        client = McpClient(config)
        tools = await client.list_tools()
        return {
            "ok": True,
            "server": public_mcp_config(config),
            "news_import": news_import_capability(tools),
            "tools": [
                {"name": item.get("name"), "title": item.get("title") or item.get("name"), "description": item.get("description", "")[:300], "inputSchema": item.get("inputSchema", {})}
                for item in tools
            ],
        }
    except (ValueError, OSError, httpx.HTTPError, json.JSONDecodeError) as exc:
        return {"ok": False, "message": f"MCP 连接失败：{safe_mcp_error(exc, config.get('authorization', ''))}", "tools": []}


@app.post("/api/mcp/fetch")
async def fetch_mcp_news(body: McpFetchInput):
    validate_range(body.date_from, body.date_to)
    if body.dimension not in topic_dimensions(body.topic):
        raise HTTPException(422, "主题与维度不匹配")
    config = {}
    try:
        config = load_mcp_config(body.server_id)
        client = McpClient(config)
        capability = news_import_capability(await client.list_tools())
        if not capability["supported"]:
            raise ValueError("所选 MCP 服务不支持新闻导入，缺少工具：" + "、".join(capability["missing_tools"]))
        service = await client.call("service_status", {})
        if not service.get("ok") or service.get("data", {}).get("overall") not in {None, "ok", "degraded"}:
            raise ValueError("MCP 服务状态不可用")
        arguments = {"query": body.query, "top_k": body.top_k, "mode": "hybrid"}
        if body.date_from:
            arguments["date_from"] = body.date_from
        if body.date_to:
            arguments["date_to"] = body.date_to
        envelope = await client.call("knowledge_search", arguments)
        _, fetched = store_news(config, body.query, body.topic, body.dimension, envelope)
        root = collector_read_root()
        bundle = load_bundle(root)
        sid, created = save_snapshot(bundle)
        set_working_snapshot_id(sid)
        start_translation()
        return {"ok": True, "server_id": config.get("id"), "id": sid, "created": created, "fetched": fetched, "count": len(bundle["records"]), "errors": bundle["errors"]}
    except (ValueError, OSError, KeyError, TypeError, httpx.HTTPError, json.JSONDecodeError) as exc:
        raise HTTPException(422, f"MCP 新闻导入失败：{safe_mcp_error(exc, config.get('authorization', ''))}") from exc

@app.post("/api/model/check")
async def check_model():
    if not llm.status()['configured']:
        return {"ok":False,"message":"尚未配置后台模型，请在连接配置的后台模型页填写并保存"}
    return await test_translation()

@app.post("/api/import")
def import_data():
    try:
        sync = {"projects": 0, "files": 0, "bytes": 0}
        configured = os.getenv("CRAWLER_ROOT")
        if not configured or Path(configured).resolve() == collector_data_root().resolve():
            sync = sync_local_collector_outputs(data_root=collector_data_root())
        # Synchronize first: otherwise a first local run would select demo data
        # before the published runtime directory has been populated.
        root = collector_read_root()
        if not root.is_dir():
            raise HTTPException(400, "爬虫目录不存在，请在 .env 设置 CRAWLER_ROOT")
        bundle = load_bundle(root)
        sid, created = save_snapshot(bundle)
        # New published data becomes the active research context so the three
        # analysis pages can reload and invalidate their model-summary keys.
        set_working_snapshot_id(sid)
        start_translation()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"数据导入未完成：{type(exc).__name__}：{str(exc)[:180]}") from exc
    return {"id": sid, "created": created, "errors": bundle["errors"], "count": len(bundle["records"]), "sync": sync}

@app.get("/api/workspace")
def workspace(snapshot_id: str, topic: Topic = "all", dimension: str = "", include_records: bool = True, start: str = Query("", pattern=r"^(\d{4}-\d{2}-\d{2})?$"), end: str = Query("", pattern=r"^(\d{4}-\d{2}-\d{2})?$")):
    s = snapshot_or_404(snapshot_id)
    validate_range(start, end)
    records = filtered_records(s, topic, dimension, start, end)
    records.sort(key=lambda r: r["published_at"] or "", reverse=True)
    a = analysis(s, topic, dimension, start, end)
    cache=translation_cache()
    reviews=review_index(snapshot_id)
    counts = risk_panel_counts(records, reviews)
    map_records = [apply_review(present_map_record(r, cache), reviews.get(r["id"])) for r in records] if include_records else []
    return {"snapshot": {"id": s["id"], "created_at": s["created_at"], "stats": s["import_stats"], "errors": s["errors"], "cleaning": s.get("cleaning", {})}, "topics": coverage(s, start, end), "records": map_records, **counts, "metrics": metrics(s, topic, dimension, start, end), "projects":s.get("projects",[]), "sources": [{**x,'name':source_name(x['name'],cache)} for x in s["sources"]], **a}


@app.get("/api/map-records")
def map_records(snapshot_id: str, topic: Topic = "all", dimension: str = "", start: str = Query("", pattern=r"^(\d{4}-\d{2}-\d{2})?$"), end: str = Query("", pattern=r"^(\d{4}-\d{2}-\d{2})?$")):
    """Return map-only evidence on demand instead of attaching it to every page."""
    s = snapshot_or_404(snapshot_id)
    validate_range(start, end)
    records = filtered_records(s, topic, dimension, start, end)
    records.sort(key=lambda r: r["published_at"] or "", reverse=True)
    cache = translation_cache()
    reviews = review_index(snapshot_id)
    result = []
    for record in records:
        projected = present_map_record(record, cache)
        if projected["points"]:
            result.append(apply_review(projected, reviews.get(record["id"])))
    return result


@app.post("/api/page-summary")
async def generate_page_summary(body: PageSummaryRequest):
    validate_range(body.start, body.end)
    snapshot = working_snapshot_or_409(body.snapshot_id)
    return await page_summary(snapshot, body.page, body.topic, body.dimension, body.start, body.end)

@app.get("/api/evidence")
def evidence_page(snapshot_id: str, topic: Topic = "all", dimension: str = "", q: str = Query("", max_length=300), offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), start: str = Query("", pattern=r"^(\d{4}-\d{2}-\d{2})?$"), end: str = Query("", pattern=r"^(\d{4}-\d{2}-\d{2})?$")):
    s = snapshot_or_404(snapshot_id)
    validate_range(start, end)
    records = filtered_records(s, topic, dimension, start, end)
    cache = translation_cache()
    from .semantic import search_evidence
    records = search_evidence(records, q)
    total = len(records)
    page = records[offset:offset + limit]
    reviews = review_index(snapshot_id)
    return {
        "records": [apply_review(present_record_summary(record, cache), reviews.get(record["id"])) for record in page],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
    }

@app.get("/api/evidence/{rid}")
def evidence(rid: str, snapshot_id: str):
    s = snapshot_or_404(snapshot_id)
    record = next((r for r in s["records"] if r["id"] == rid), None)
    if not record:
        raise HTTPException(404, "证据不在当前快照内")
    return {**apply_review(present_record(record),review_index(snapshot_id).get(rid)),"link_health":link_health(snapshot_id,record.get("url",""))}

@app.post("/api/evidence/{rid}/translate")
async def translate_evidence(rid:str,snapshot_id:str):
    from .translation_service import translate_items
    from .chinese import save_translation
    record=next((r for r in snapshot_or_404(snapshot_id)['records'] if r['id']==rid),None)
    if not record:raise HTTPException(404,"证据不在当前快照")
    try:
        pending = [{"id": field, "text": record[field]} for field in ("title", "text") if record.get(field, "").strip()]
        result = await translate_items(pending)
        if result["failed"]:
            raise ValueError("translation_quality_failed")
        for item in result["items"]:
            save_translation(record[item["id"]], item["text"], '模型翻译，待语义核验')
        return {"ok":True,"message":"已生成机器译文，请对照原文核验语义","model":result["model"]}
    except Exception as exc:return {"ok":False,"message":llm.error_message(exc)+"；未替换现有译文"}


@app.post("/api/translations/titles")
async def translate_titles(body: TranslationBatchInput):
    """Translate only the headlines currently opened in a map cluster.

    This intentionally stays small and on demand.  Sending the complete
    snapshot to a remote model would add latency and cost, and it would make a
    map click wait for unrelated historical records.
    """
    from .translation_service import translate_items
    from .chinese import save_translation, is_chinese

    snapshot = snapshot_or_404(body.snapshot_id)
    by_id = {record["id"]: record for record in snapshot["records"]}
    requested = list(dict.fromkeys(body.record_ids))
    missing = [record_id for record_id in requested if record_id not in by_id]
    if missing:
        raise HTTPException(404, "部分证据不在当前快照内")

    cache = translation_cache()
    pending = [
        {"id": record_id, "text": by_id[record_id].get("title", "")}
        for record_id in requested
        if by_id[record_id].get("title")
        and not is_chinese(by_id[record_id]["title"])
        and digest(by_id[record_id]["title"]) not in cache
    ]
    if not pending:
        return {"ok": True, "translated": 0, "failed": [], "titles": {}, "message": "当前新闻标题已有中文译文"}

    try:
        result = await translate_items(pending)
        originals = {item["id"]: item["text"] for item in pending}
        translated: dict[str, str] = {}
        failed = [item["id"] for item in result["failed"]]
        for item in result["items"]:
            record_id, value = item["id"], item["text"]
            save_translation(originals[record_id], value, "模型翻译，待语义核验")
            translated[record_id] = value
        return {
            "ok": bool(translated),
            "translated": len(translated),
            "failed": failed,
            "titles": translated,
            "model": result["model"],
            "message": (
                f"已生成 {len(translated)} 条中文标题，请打开原始来源核验"
                if translated else "模型未返回通过基础校验的中文标题"
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "translated": 0,
            "failed": [item["id"] for item in pending],
            "titles": {},
            "message": llm.error_message(exc) + "；保留原始来源入口",
        }

@app.get("/api/link-audit")
def link_audit(snapshot_id:str):
    snapshot_or_404(snapshot_id)
    return link_summary(snapshot_id)

@app.get("/api/archive/{name}")
def archive(name: str):
    if not __import__('re').fullmatch(r"[a-f0-9]{24}\.(json|jsonl|csv|md|html|xml|txt)", name):
        raise HTTPException(404)
    file = data_dir() / "archive" / name
    if not file.exists():
        raise HTTPException(404)
    return FileResponse(file, filename=name,
                        media_type="text/plain; charset=utf-8" if file.suffix in {".md", ".html", ".xml", ".txt"} else "text/csv" if file.suffix==".csv" else "application/json",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"})

class Ask(BaseModel):
    previous_answer_id: str = Field(default="",max_length=64)
    snapshot_id: str
    question: str = Field(min_length=1, max_length=3000)
    topic: Topic = "all"
    dimension: str = ""
    start: str = ""
    end: str = ""
    use_model: bool = True

@app.post("/api/ask")
async def ask(body: Ask):
    validate_range(body.start, body.end)
    snapshot = working_snapshot_or_409(body.snapshot_id)
    return present_answer(await answer(snapshot, body.question, body.topic, body.dimension, body.start, body.end, body.use_model,body.previous_answer_id))

@app.get("/api/answers")
def answers(snapshot_id: str):
    snapshot_or_404(snapshot_id)
    with db() as c:
        rows = c.execute("SELECT payload FROM answers WHERE snapshot_id=? ORDER BY created_at DESC LIMIT 30", (snapshot_id,)).fetchall()
    return [present_answer(json.loads(r[0])) for r in rows]

class BriefRequest(BaseModel):
    snapshot_id: str
    use_model: bool = True
    template_id: str = Field(default="", max_length=80)
    force: bool = False

class BriefTemplateRequest(BaseModel):
    config: dict

@app.get("/api/brief-templates")
def brief_templates():
    return list_templates()

@app.post("/api/brief-templates")
def create_brief_template(body: BriefTemplateRequest):
    try:
        return save_template(body.config)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.put("/api/brief-templates/{template_id}")
def update_brief_template(template_id: str, body: BriefTemplateRequest):
    try:
        return save_template(body.config, template_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.put("/api/brief-templates/{template_id}/default")
def default_brief_template(template_id: str):
    try:
        return set_default_brief_template(template_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

@app.delete("/api/brief-templates/{template_id}")
def remove_brief_template(template_id: str):
    try:
        return delete_brief_template(template_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.post("/api/briefs")
async def new_brief(body: BriefRequest):
    if body.template_id and not get_template(body.template_id):
        raise HTTPException(404, "简报模板不存在")
    return await create_brief(working_snapshot_or_409(body.snapshot_id), body.use_model, body.template_id, body.force)

@app.get("/api/briefs")
def briefs(snapshot_id: str = ""):
    with db() as c:
        rows = c.execute("""SELECT b.id,b.snapshot_id,b.created_at,b.mode,
                                   m.template_id,m.template_version,m.model,m.window_start,m.window_end,
                                   m.payload AS metadata_payload
                            FROM briefs b LEFT JOIN brief_metadata m ON m.brief_id=b.id
                            WHERE (?='' OR b.snapshot_id=?) ORDER BY b.created_at DESC""", (snapshot_id, snapshot_id)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        raw_metadata = item.pop("metadata_payload", None)
        try:
            metadata = json.loads(raw_metadata) if raw_metadata else {}
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        item["generation_status"] = metadata.get("generation_status", {})
        result.append(item)
    return result

@app.get("/api/briefs/{bid}")
def brief_detail(bid: str):
    with db() as c:
        row = c.execute("""SELECT b.*,m.template_id,m.template_version,m.model,m.window_start,m.window_end,
                                  m.payload AS metadata_payload
                           FROM briefs b LEFT JOIN brief_metadata m ON m.brief_id=b.id
                           WHERE b.id=?""", (bid,)).fetchone()
    if not row:
        raise HTTPException(404)
    item = dict(row)
    raw_metadata = item.pop("metadata_payload", None)
    try:
        metadata = json.loads(raw_metadata) if raw_metadata else {}
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    item["generation_status"] = metadata.get("generation_status", {})
    item["markdown"] = localize_brief_markdown(row["markdown"])
    return item

@app.get("/api/briefs/{bid}/download")
def download(bid: str):
    with db() as c:
        row = c.execute("SELECT markdown FROM briefs WHERE id=?", (bid,)).fetchone()
    if not row:
        raise HTTPException(404)
    return PlainTextResponse(localize_brief_markdown(row[0]), headers={"Content-Disposition": f'attachment; filename="geo-brief-{bid}.md"'})

@app.get("/api/brief-diff")
def brief_diff(before: str, after: str):
    with db() as c:
        a = c.execute("SELECT markdown FROM briefs WHERE id=?", (before,)).fetchone()
        b = c.execute("SELECT markdown FROM briefs WHERE id=?", (after,)).fetchone()
    if not a or not b:
        raise HTTPException(404)
    from .chinese import compare_brief_markdown
    return {"diff": compare_brief_markdown(a[0], b[0], before, after)}

SIGNAL_LABELS = {"escalation":"升级相关","sustain":"持续相关","easing":"缓和相关","unknown":"待研判"}
def review_index(sid):
    from .semantic import review_index as shared_index
    return shared_index(sid)

def apply_review(record, review):
    from .semantic import apply_review as shared_review
    return shared_review(record, review)

class ReviewInput(BaseModel):
    signal: Literal["escalation","sustain","easing","unknown"]
    reviewer: str = Field(min_length=1,max_length=80)
    reason: str = Field(min_length=1,max_length=1500)
    version: int = Field(default=0,ge=0)

@app.get("/api/reviews/{rid}")
def review_history(rid: str,snapshot_id:str):
    evidence(rid,snapshot_id)
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM reviews WHERE snapshot_id=? AND record_id=? ORDER BY version DESC",(snapshot_id,rid))]

@app.post("/api/reviews/{rid}")
def save_review(rid: str,snapshot_id:str,body:ReviewInput):
    evidence(rid,snapshot_id)
    if not body.reviewer.strip() or not body.reason.strip():
        raise HTTPException(422,"请填写研究员姓名与判断依据")
    from .store import now
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        version=c.execute("SELECT COALESCE(MAX(version),0) FROM reviews WHERE snapshot_id=? AND record_id=?",(snapshot_id,rid)).fetchone()[0]
        if version!=body.version:
            raise HTTPException(409,"该记录已被更新，请重新打开详情后再提交")
        c.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",(snapshot_id,rid,version+1,body.signal,body.reviewer.strip(),body.reason.strip(),now()))
    return evidence(rid,snapshot_id)

if (ROOT / "dist").exists():
    app.mount("/", StaticFiles(directory=ROOT / "dist", html=True), name="frontend")
