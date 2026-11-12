"""采集管线 REST 接口：/api/pipelines。

仅做 HTTP 适配：权限、版本冲突、校验等业务规则都在 pipeline_service / pipeline_runs。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from . import web_auth
from .pipeline_config import default_resolve
from .pipeline_runs import (BUILTIN_NO_TRIAL, TRIAL_CONFLICT, list_run_items, list_runs, preview_pipeline, run_trial,
                            trial_running)
from .pipeline_scheduler import pipelines_enabled
from .pipeline_service import (Actor, PipelineError, create_pipeline, delete_pipeline, get_pipeline,
                               list_pipelines, list_versions, rollback, set_enabled, update_pipeline)
from .remote_collection import collection_mode
from .store import db

log = logging.getLogger(__name__)

STATUS_BY_CODE = {"NOT_FOUND": 404, "FORBIDDEN": 403, "VERSION_CONFLICT": 409, "INVALID": 422,
                  "BUILTIN_READONLY": 422}


def _feature_enabled(response: Response) -> None:
    if not pipelines_enabled():
        raise HTTPException(404, "Not Found")
    response.headers["Cache-Control"] = "no-store"


def actor_of(request: Request) -> Actor:
    if not web_auth.enabled():
        return Actor("local", True)
    username = getattr(request.state, "web_user", None)
    if not username:
        raise HTTPException(401, "请先登录")
    return Actor(username, username in web_auth.users())


def require_admin(request: Request, _flag: None = Depends(_feature_enabled)) -> None:
    """所有 /api/pipelines* 仅管理员可用（含只读）；排在功能开关之后、同源与远端检查之前。"""
    if not actor_of(request).is_admin:
        raise HTTPException(403, "仅管理员可以管理采集管线")


router = APIRouter(prefix="/api/pipelines", tags=["pipelines"], dependencies=[Depends(require_admin)])


def _write(request: Request) -> Actor:
    """写操作与运行：同源校验、远端模式拒绝、解析操作者。"""
    web_auth.check_origin(request)
    if collection_mode() == "remote":
        raise HTTPException(409, "远端采集模式下此操作需在采集端（Sealos）处理")
    return actor_of(request)


def _http(exc: PipelineError) -> HTTPException:
    if exc.code == "PREVIEW_FAILED":
        return HTTPException(502, {"message": exc.message})
    return HTTPException(STATUS_BY_CODE.get(exc.code, 400), {"code": exc.code, "message": exc.message, **exc.details})


class PipelineBody(BaseModel):
    row: dict[str, Any]
    config: dict[str, Any]
    change_note: str = ""


class PatchBody(BaseModel):
    row: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] | None = None
    change_note: str = ""


class PreviewBody(BaseModel):
    row: dict[str, Any]
    config: dict[str, Any]
    limit: int = Field(5, ge=1, le=20)


class RollbackBody(BaseModel):
    to_version: int
    expected_version: int


@router.get("")
def list_all(topic: str | None = None, status: str | None = None, kind: str | None = None,
             q: str | None = None, changed_since: str | None = None):
    since = None
    if changed_since:
        try:
            since = datetime.fromisoformat(changed_since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, {"code": "INVALID", "message": "changed_since 不是有效的时间",
                                      "field": "changed_since"}) from None
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
    try:
        items = list_pipelines(topic=topic, status=status, kind=kind, q=q, changed_since=since)
    except PipelineError as exc:
        raise _http(exc) from exc
    return {"items": items, "server_time": datetime.now(timezone.utc).isoformat()}


@router.post("", status_code=201)
def create(body: PipelineBody, actor: Actor = Depends(_write)):
    try:
        return create_pipeline(actor, body.row, body.config, body.change_note, resolve=default_resolve)
    except PipelineError as exc:
        raise _http(exc) from exc


@router.post("/preview")
def preview(body: PreviewBody, actor: Actor = Depends(_write)):
    try:
        return preview_pipeline(body.row, body.config, limit=body.limit)
    except PipelineError as exc:
        raise _http(exc) from exc


@router.get("/{pipeline_id}")
def get_one(pipeline_id: str):
    try:
        return get_pipeline(pipeline_id)
    except PipelineError as exc:
        raise _http(exc) from exc


@router.patch("/{pipeline_id}")
def patch(pipeline_id: str, body: PatchBody, actor: Actor = Depends(_write),
          if_match: str | None = Header(None)):
    if if_match is None:
        raise HTTPException(428, "缺少 If-Match 版本号")
    try:
        expected = int(if_match.strip().strip('"'))
    except ValueError:
        raise HTTPException(422, "If-Match 必须是整数版本号") from None
    try:
        return update_pipeline(actor, pipeline_id, expected_version=expected, row_patch=body.row,
                               config=body.config, change_note=body.change_note, resolve=default_resolve)
    except PipelineError as exc:
        raise _http(exc) from exc


@router.delete("/{pipeline_id}", status_code=204)
def delete(pipeline_id: str, actor: Actor = Depends(_write)):
    try:
        delete_pipeline(actor, pipeline_id)
    except PipelineError as exc:
        raise _http(exc) from exc
    return Response(status_code=204)


@router.post("/{pipeline_id}/enable")
def enable(pipeline_id: str, actor: Actor = Depends(_write)):
    try:
        return set_enabled(actor, pipeline_id, True)
    except PipelineError as exc:
        raise _http(exc) from exc


@router.post("/{pipeline_id}/disable")
def disable(pipeline_id: str, actor: Actor = Depends(_write)):
    try:
        return set_enabled(actor, pipeline_id, False)
    except PipelineError as exc:
        raise _http(exc) from exc


@router.post("/{pipeline_id}/rollback")
def roll_back(pipeline_id: str, body: RollbackBody, actor: Actor = Depends(_write)):
    try:
        return rollback(actor, pipeline_id, to_version=body.to_version, expected_version=body.expected_version,
                        resolve=default_resolve)
    except PipelineError as exc:
        raise _http(exc) from exc


def _background_trial(pipeline_id: str, username: str) -> None:
    try:
        run_trial(pipeline_id, actor=username)
    except PipelineError as exc:
        log.warning("管线 %s 测试运行未执行：%s", pipeline_id, exc.message)
    except Exception:  # noqa: BLE001 - 后台线程不能让异常外泄
        log.exception("管线 %s 测试运行失败", pipeline_id)


@router.post("/{pipeline_id}/trial", status_code=202)
def trial(pipeline_id: str, actor: Actor = Depends(_write)):
    """测试运行：产物写到测试目录、不进快照、不写去重台账；不检查整批服务锁，整批进行中也可发起。"""
    try:
        pipeline = get_pipeline(pipeline_id)
    except PipelineError as exc:
        raise _http(exc) from exc
    if pipeline["kind"] != "configurable":
        raise HTTPException(422, {"code": "INVALID", "message": BUILTIN_NO_TRIAL})
    if trial_running(pipeline_id):
        raise HTTPException(409, TRIAL_CONFLICT)
    threading.Thread(target=_background_trial, args=(pipeline_id, actor.username), daemon=True,
                     name=f"pipeline-trial-{pipeline_id}").start()
    return {"pipeline_id": pipeline_id, "accepted": True}


@router.get("/{pipeline_id}/versions")
def versions(pipeline_id: str):
    try:
        return list_versions(pipeline_id)
    except PipelineError as exc:
        raise _http(exc) from exc


@router.get("/{pipeline_id}/runs")
def runs(pipeline_id: str, limit: int = Query(20, ge=1, le=200)):
    try:
        get_pipeline(pipeline_id)
    except PipelineError as exc:
        raise _http(exc) from exc
    return list_runs(pipeline_id, limit)


@router.get("/{pipeline_id}/runs/{run_id}/items")
def run_items(pipeline_id: str, run_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    with db() as conn:
        found = conn.execute("SELECT 1 FROM collection_pipeline_runs WHERE run_id=%s AND pipeline_id=%s "
                             "AND NOT is_deleted", (run_id, pipeline_id)).fetchone()
        if not found:
            raise HTTPException(404, "运行记录不存在")
        total = conn.execute("SELECT count(*) AS n FROM collection_pipeline_run_items WHERE run_id=%s AND NOT is_deleted",
                             (run_id,)).fetchone()["n"]
    return {"items": list_run_items(run_id, offset=offset, limit=limit), "total": total}
