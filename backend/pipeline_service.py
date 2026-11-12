"""采集管线服务：增删改查、版本、权限。

REST 接口与后续 MCP 工具共用此模块。所有写操作在同一事务内对管线行 SELECT … FOR UPDATE，
并维护 updated_by / updated_at；data_timestamp 不随更新改变。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import psycopg

from backend.pipeline_config import (PipelineConfigError, _validate_row, default_resolve, next_run_at,
                                     validate_pipeline, validate_schedule)
from backend.store import db

STATUSES = ("enabled", "disabled", "running", "success", "partial", "failed")
ROW_FIELDS = {"name", "description", "topic", "interval_sec", "daily_times", "timezone"}
SCHEDULE_FIELDS = {"interval_sec", "daily_times", "timezone"}
BUILTIN_EDITABLE = SCHEDULE_FIELDS | {"enabled"}


class PipelineError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(f"{code}：{message}")
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class Actor:
    username: str
    is_admin: bool
    channel: str = "web"


def can_edit(actor: Actor, pipeline: dict) -> bool:
    if actor.is_admin:
        return True
    if pipeline.get("kind") == "builtin":
        return False
    return pipeline.get("owner") == actor.username


def _invalid(exc: PipelineConfigError) -> PipelineError:
    return PipelineError("INVALID", exc.message, {"field": exc.field})


def _lock(conn, pipeline_id: str) -> dict:
    row = conn.execute("SELECT * FROM collection_pipelines WHERE id=%s AND NOT is_deleted FOR UPDATE",
                       (pipeline_id,)).fetchone()
    if not row:
        raise PipelineError("NOT_FOUND", "管线不存在或已删除", {"id": pipeline_id})
    return row


def _config(conn, pipeline_id: str, version: int) -> dict | None:
    row = conn.execute("SELECT config FROM collection_pipeline_versions WHERE pipeline_id=%s AND version=%s AND NOT is_deleted",
                       (pipeline_id, version)).fetchone()
    return row["config"] if row else None


def _with_config(conn, row: dict) -> dict:
    row = dict(row)
    row["config"] = _config(conn, row["id"], row["current_version"])
    return row


def _check_edit(actor: Actor, row: dict) -> None:
    if not can_edit(actor, row):
        raise PipelineError("FORBIDDEN", "没有权限修改此管线", {"owner": row["owner"]})


def _check_version(row: dict, expected_version: int) -> None:
    if row["current_version"] != expected_version:
        raise PipelineError("VERSION_CONFLICT", "管线已被他人修改，请刷新后重试",
                            {"current_version": row["current_version"]})


def _add_version(conn, actor: Actor, pipeline_id: str, version: int, config: dict, note: str) -> None:
    conn.execute(
        "INSERT INTO collection_pipeline_versions(pipeline_id,version,config,change_note,source_channel,created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pipeline_id, version, json.dumps(config, ensure_ascii=False), note, actor.channel, actor.username))


def _schedule_next(row: dict, now: datetime) -> datetime | None:
    return next_run_at(row, now)


def list_pipelines(*, topic: str | None = None, status: str | None = None, kind: str | None = None,
                   q: str | None = None, changed_since: datetime | None = None) -> list[dict]:
    if status is not None and status not in STATUSES:
        raise PipelineError("INVALID", f"status 只能是 {'、'.join(STATUSES)}", {"field": "status"})
    where, params = ["NOT p.is_deleted"], []
    if topic:
        where.append("p.topic=%s")
        params.append(topic)
    if kind:
        where.append("p.kind=%s")
        params.append(kind)
    if status in ("enabled", "disabled"):
        where.append("p.enabled=%s")
        params.append(status == "enabled")
    elif status:
        where.append("p.last_status=%s")
        params.append(status)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(p.name ILIKE %s OR p.description ILIKE %s)")
        params += [like, like]
    if changed_since is not None:
        where.append("(p.created_at > %s OR p.updated_at > %s OR p.last_finished_at > %s)")
        params += [changed_since] * 3
    sql = ("SELECT p.*, COALESCE(jsonb_array_length(v.config->'sources'),0) AS source_count "
           "FROM collection_pipelines p LEFT JOIN collection_pipeline_versions v "
           "ON v.pipeline_id=p.id AND v.version=p.current_version AND NOT v.is_deleted "
           "WHERE " + " AND ".join(where) + " ORDER BY p.name, p.id")
    with db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_pipeline(pipeline_id: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM collection_pipelines WHERE id=%s AND NOT is_deleted", (pipeline_id,)).fetchone()
        if not row:
            raise PipelineError("NOT_FOUND", "管线不存在或已删除", {"id": pipeline_id})
        return _with_config(conn, row)


def list_versions(pipeline_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM collection_pipeline_versions WHERE pipeline_id=%s AND NOT is_deleted ORDER BY version DESC",
            (pipeline_id,)).fetchall()
        return [dict(r) for r in rows]


def create_pipeline(actor: Actor, row: dict, config: dict, change_note: str = "", *,
                    resolve: Callable[[str], list[str]] = default_resolve) -> dict:
    try:
        clean_row, clean_config = validate_pipeline(row, config, resolve=resolve)
    except PipelineConfigError as exc:
        raise _invalid(exc) from exc
    pid = "p-" + uuid.uuid4().hex[:12]
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO collection_pipelines(id,kind,name,description,topic,enabled,current_version,interval_sec,"
                "daily_times,timezone,owner,created_by) VALUES (%s,'configurable',%s,%s,%s,false,1,%s,%s,%s,%s,%s)",
                (pid, clean_row["name"], clean_row["description"], clean_row["topic"], clean_row["interval_sec"],
                 clean_row["daily_times"] or [], clean_row["timezone"], actor.username, actor.username))
            _add_version(conn, actor, pid, 1, clean_config, change_note)
    except psycopg.errors.UniqueViolation as exc:
        raise PipelineError("INVALID", "已存在同名管线", {"field": "row.name"}) from exc
    return get_pipeline(pid)


def update_pipeline(actor: Actor, pipeline_id: str, *, expected_version: int, row_patch: dict,
                    config: dict | None, change_note: str = "",
                    resolve: Callable[[str], list[str]] = default_resolve) -> dict:
    row_patch = row_patch or {}
    unknown = set(row_patch) - ROW_FIELDS
    if unknown:
        raise PipelineError("INVALID", f"不支持修改的字段：{'、'.join(sorted(unknown))}", {"field": sorted(unknown)[0]})
    try:
        with db() as conn:
            cur = _lock(conn, pipeline_id)
            _check_edit(actor, cur)
            if cur["kind"] == "builtin":
                bad = set(row_patch) - SCHEDULE_FIELDS
                if bad or config is not None:
                    raise PipelineError("BUILTIN_READONLY", "内置管线只允许修改启停与调度", {"fields": sorted(bad)})
            _check_version(cur, expected_version)
            merged = {k: cur[k] for k in ROW_FIELDS}
            merged["daily_times"] = merged["daily_times"] or None
            merged.update(row_patch)
            new_version = cur["current_version"]
            clean_config = None
            try:
                if cur["kind"] == "builtin":
                    clean_row = {**merged, **validate_schedule(merged)}
                elif config is not None:
                    clean_row, clean_config = validate_pipeline(merged, config, resolve=resolve)
                else:
                    clean_row = _validate_row(merged)
            except PipelineConfigError as exc:
                raise _invalid(exc) from exc
            if clean_config is not None and clean_config != _config(conn, pipeline_id, new_version):
                new_version += 1
                _add_version(conn, actor, pipeline_id, new_version, clean_config, change_note)
            now = datetime.now(timezone.utc)
            daily = clean_row["daily_times"] or []
            sched_changed = (clean_row["interval_sec"], daily, clean_row["timezone"]) != \
                            (cur["interval_sec"], cur["daily_times"] or [], cur["timezone"])
            nxt = cur["next_run_at"]
            if not cur["enabled"]:
                nxt = None
            elif sched_changed:
                nxt = _schedule_next({**clean_row, "daily_times": daily}, now)
            conn.execute(
                "UPDATE collection_pipelines SET name=%s,description=%s,topic=%s,interval_sec=%s,daily_times=%s,"
                "timezone=%s,current_version=%s,next_run_at=%s,updated_by=%s,updated_at=%s WHERE id=%s",
                (clean_row["name"], clean_row["description"], clean_row["topic"], clean_row["interval_sec"], daily,
                 clean_row["timezone"], new_version, nxt, actor.username, now, pipeline_id))
    except psycopg.errors.UniqueViolation as exc:
        raise PipelineError("INVALID", "已存在同名管线", {"field": "row.name"}) from exc
    return get_pipeline(pipeline_id)


def set_enabled(actor: Actor, pipeline_id: str, enabled: bool) -> dict:
    with db() as conn:
        cur = _lock(conn, pipeline_id)
        _check_edit(actor, cur)
        now = datetime.now(timezone.utc)
        nxt = _schedule_next(cur, now) if enabled else None
        conn.execute("UPDATE collection_pipelines SET enabled=%s,next_run_at=%s,updated_by=%s,updated_at=%s WHERE id=%s",
                     (bool(enabled), nxt, actor.username, now, pipeline_id))
    return get_pipeline(pipeline_id)


def delete_pipeline(actor: Actor, pipeline_id: str) -> None:
    with db() as conn:
        cur = _lock(conn, pipeline_id)
        _check_edit(actor, cur)
        if cur["kind"] == "builtin":
            raise PipelineError("BUILTIN_READONLY", "内置管线不能删除")
        conn.execute("UPDATE collection_pipelines SET is_deleted=true,enabled=false,next_run_at=NULL,"
                     "updated_by=%s,updated_at=%s WHERE id=%s",
                     (actor.username, datetime.now(timezone.utc), pipeline_id))


def rollback(actor: Actor, pipeline_id: str, *, to_version: int, expected_version: int) -> dict:
    with db() as conn:
        cur = _lock(conn, pipeline_id)
        _check_edit(actor, cur)
        if cur["kind"] == "builtin":
            raise PipelineError("BUILTIN_READONLY", "内置管线没有可回滚的配置版本")
        _check_version(cur, expected_version)
        old = _config(conn, pipeline_id, to_version)
        if old is None:
            raise PipelineError("NOT_FOUND", f"版本 {to_version} 不存在", {"version": to_version})
        new_version = cur["current_version"] + 1
        _add_version(conn, actor, pipeline_id, new_version, old, f"回滚到版本 {to_version}")
        conn.execute("UPDATE collection_pipelines SET current_version=%s,updated_by=%s,updated_at=%s WHERE id=%s",
                     (new_version, actor.username, datetime.now(timezone.utc), pipeline_id))
    return get_pipeline(pipeline_id)
