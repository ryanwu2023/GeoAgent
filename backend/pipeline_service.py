"""采集管线服务：增删改查、版本、权限、启停。

REST 接口与后续 MCP 工具共用此模块。所有写操作在同一事务内对管线行 SELECT … FOR UPDATE，
并维护 updated_by / updated_at；data_timestamp 不随更新改变。

管线不单独定时：启用的管线随现有整批采集运行。内置采集器（kind=builtin）的启用状态以
collector_config 的排除列表为唯一来源——注册表启用且不在 exclude_ids 内即为启用；
页面切换 builtin 启停就是改这份排除列表，与采集监控页的“排除采集器”一致。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import psycopg

from backend import collector_config
from backend.paths import project_root
from backend.pipeline_config import PipelineConfigError, default_resolve, validate_pipeline, validate_row
from backend.store import db
from scripts.integrate_collectors import load_registry

STATUSES = ("enabled", "disabled", "running", "success", "partial", "failed")
ROW_FIELDS = ("name", "description", "topics")
REGISTRY_DISABLED = "注册表已停用该采集器，不能在此启用"
# load_config() 附带的元信息，不是配置项，写回时去掉（updated_at 由 save_config 重新盖章）
CONFIG_META = {"path", "platform", "source", "updated_at"}


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
    """管线功能仅管理员可用；owner 只记录创建人，不再授予编辑权。"""
    return actor.is_admin


def _registry() -> list[dict]:
    """内置采集器注册表（collectors/collectors.json）。"""
    return load_registry(project_root() / "collectors" / "collectors.json")


def effective_builtin_enabled(registry_item: dict | None, exclude_ids) -> bool:
    """内置采集器的有效启用状态：注册表中存在且启用，并且不在 collector_config 排除列表内。"""
    if not registry_item or not registry_item.get("enabled", True):
        return False
    return registry_item["id"] not in set(exclude_ids or [])


def _with_effective_enabled(rows: list[dict]) -> list[dict]:
    """builtin 行的 enabled 按注册表与排除列表实时计算；表内值只是同步缓存。"""
    if not any(r["kind"] == "builtin" for r in rows):
        return rows
    items = {item["id"]: item for item in _registry()}
    exclude = collector_config.load_config().get("exclude_ids", [])
    for row in rows:
        if row["kind"] == "builtin":
            row["enabled"] = effective_builtin_enabled(items.get(row["id"]), exclude)
    return rows


def _write_exclusion(collector_id: str, enabled: bool) -> None:
    """启用 = 移出排除列表，停用 = 加入；只改 exclude_ids，其它配置项原样写回。"""
    items = {item["id"]: item for item in _registry()}
    item = items.get(collector_id)
    if enabled and not (item and item.get("enabled", True)):
        raise PipelineError("INVALID", REGISTRY_DISABLED, {"id": collector_id})
    if item is None:
        return  # 注册表已没有该采集器：本来就不会运行，排除列表也不接受未知 id
    config = collector_config.load_config()
    current = set(config.get("exclude_ids", []))
    wanted = current - {collector_id} if enabled else current | {collector_id}
    if wanted == current:
        return
    payload = {k: v for k, v in config.items() if k not in CONFIG_META}
    payload["exclude_ids"] = sorted(wanted)
    try:
        # 已在列表里的旧 id 原样保留，不因注册表变动而让本次保存失败
        collector_config.save_config(payload, known_ids=set(items) | current)
    except ValueError as exc:
        raise PipelineError("INVALID", str(exc), {"field": "exclude_ids"}) from exc


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


def list_pipelines(*, topic: str | None = None, status: str | None = None, kind: str | None = None,
                   q: str | None = None, changed_since: datetime | None = None) -> list[dict]:
    if status is not None and status not in STATUSES:
        raise PipelineError("INVALID", f"status 只能是 {'、'.join(STATUSES)}", {"field": "status"})
    where, params = ["NOT p.is_deleted"], []
    if topic:
        where.append("%s = ANY(p.topics)")
        params.append(topic)
    if kind:
        where.append("p.kind=%s")
        params.append(kind)
    if status and status not in ("enabled", "disabled"):
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
        rows = _with_effective_enabled([dict(r) for r in conn.execute(sql, params).fetchall()])
    if status in ("enabled", "disabled"):
        rows = [r for r in rows if r["enabled"] == (status == "enabled")]
    return rows


def get_pipeline(pipeline_id: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM collection_pipelines WHERE id=%s AND NOT is_deleted", (pipeline_id,)).fetchone()
        if not row:
            raise PipelineError("NOT_FOUND", "管线不存在或已删除", {"id": pipeline_id})
        row = _with_config(conn, row)
    return _with_effective_enabled([row])[0]


def list_versions(pipeline_id: str) -> list[dict]:
    with db() as conn:
        if not conn.execute("SELECT 1 FROM collection_pipelines WHERE id=%s AND NOT is_deleted", (pipeline_id,)).fetchone():
            raise PipelineError("NOT_FOUND", "管线不存在或已删除", {"id": pipeline_id})
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
                "INSERT INTO collection_pipelines(id,kind,name,description,topics,enabled,current_version,owner,created_by) "
                "VALUES (%s,'configurable',%s,%s,%s,false,1,%s,%s)",
                (pid, clean_row["name"], clean_row["description"], clean_row["topics"], actor.username, actor.username))
            _add_version(conn, actor, pid, 1, clean_config, change_note)
    except psycopg.errors.UniqueViolation as exc:
        raise PipelineError("INVALID", "已存在同名管线", {"field": "row.name"}) from exc
    return get_pipeline(pid)


def update_pipeline(actor: Actor, pipeline_id: str, *, expected_version: int, row_patch: dict,
                    config: dict | None, change_note: str = "",
                    resolve: Callable[[str], list[str]] = default_resolve) -> dict:
    row_patch = row_patch or {}
    unknown = set(row_patch) - set(ROW_FIELDS)
    if unknown:
        raise PipelineError("INVALID", f"不支持修改的字段：{'、'.join(sorted(unknown))}", {"field": sorted(unknown)[0]})
    try:
        with db() as conn:
            cur = _lock(conn, pipeline_id)
            _check_edit(actor, cur)
            if cur["kind"] == "builtin":
                raise PipelineError("BUILTIN_READONLY", "内置管线只允许修改启停", {"fields": sorted(row_patch)})
            _check_version(cur, expected_version)
            merged = {k: cur[k] for k in ROW_FIELDS}
            merged.update(row_patch)
            new_version = cur["current_version"]
            clean_config = None
            try:
                if config is not None:
                    clean_row, clean_config = validate_pipeline(merged, config, resolve=resolve)
                else:
                    clean_row = validate_row(merged)
            except PipelineConfigError as exc:
                raise _invalid(exc) from exc
            if clean_config is not None and clean_config != _config(conn, pipeline_id, new_version):
                new_version += 1
                _add_version(conn, actor, pipeline_id, new_version, clean_config, change_note)
            conn.execute(
                "UPDATE collection_pipelines SET name=%s,description=%s,topics=%s,current_version=%s,"
                "updated_by=%s,updated_at=%s WHERE id=%s",
                (clean_row["name"], clean_row["description"], clean_row["topics"], new_version, actor.username,
                 datetime.now(timezone.utc), pipeline_id))
    except psycopg.errors.UniqueViolation as exc:
        raise PipelineError("INVALID", "已存在同名管线", {"field": "row.name"}) from exc
    return get_pipeline(pipeline_id)


def set_enabled(actor: Actor, pipeline_id: str, enabled: bool) -> dict:
    """启用或停用管线。builtin 同步写 collector_config 排除列表；新建的配置化管线默认停用。"""
    with db() as conn:
        cur = _lock(conn, pipeline_id)
        _check_edit(actor, cur)
        if cur["kind"] == "builtin":
            _write_exclusion(pipeline_id, bool(enabled))
        conn.execute("UPDATE collection_pipelines SET enabled=%s,updated_by=%s,updated_at=%s WHERE id=%s",
                     (bool(enabled), actor.username, datetime.now(timezone.utc), pipeline_id))
    return get_pipeline(pipeline_id)


def delete_pipeline(actor: Actor, pipeline_id: str) -> None:
    with db() as conn:
        cur = _lock(conn, pipeline_id)
        _check_edit(actor, cur)
        if cur["kind"] == "builtin":
            raise PipelineError("BUILTIN_READONLY", "内置管线不能删除")
        conn.execute("UPDATE collection_pipelines SET is_deleted=true,enabled=false,updated_by=%s,updated_at=%s "
                     "WHERE id=%s",
                     (actor.username, datetime.now(timezone.utc), pipeline_id))


def rollback(actor: Actor, pipeline_id: str, *, to_version: int, expected_version: int,
             resolve: Callable[[str], list[str]] = default_resolve) -> dict:
    with db() as conn:
        cur = _lock(conn, pipeline_id)
        _check_edit(actor, cur)
        if cur["kind"] == "builtin":
            raise PipelineError("BUILTIN_READONLY", "内置管线没有可回滚的配置版本")
        _check_version(cur, expected_version)
        old = _config(conn, pipeline_id, to_version)
        if old is None:
            raise PipelineError("NOT_FOUND", f"版本 {to_version} 不存在", {"version": to_version})
        if to_version == cur["current_version"]:
            raise PipelineError("INVALID", "不能回滚到当前版本", {"field": "to_version"})
        row = {k: cur[k] for k in ROW_FIELDS}
        try:
            _, old = validate_pipeline(row, old, resolve=resolve)
        except PipelineConfigError as exc:
            raise _invalid(exc) from exc
        new_version = cur["current_version"] + 1
        _add_version(conn, actor, pipeline_id, new_version, old, f"回滚到版本 {to_version}")
        conn.execute("UPDATE collection_pipelines SET current_version=%s,updated_by=%s,updated_at=%s WHERE id=%s",
                     (new_version, actor.username, datetime.now(timezone.utc), pipeline_id))
    return get_pipeline(pipeline_id)
