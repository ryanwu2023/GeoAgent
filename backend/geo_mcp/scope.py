"""GeoAgent-owned snapshot and tool scope checks."""

from __future__ import annotations

from .contracts import MCPError, Principal


def _allows(values: frozenset[str], value: str | None) -> bool:
    return bool(value) and ("*" in values or value in values)


def _snapshot_allowed(principal: Principal, snapshot_id: str, *, working_snapshot_id: str | None) -> bool:
    allowed = principal.allowed_snapshot_ids
    if "*" in allowed:
        return True
    if snapshot_id in allowed:
        return True
    return "working" in allowed and snapshot_id == working_snapshot_id


def authorize(
    principal: Principal,
    *,
    tool: str,
    snapshot_id: str | None = None,
    working_snapshot_id: str | None = None,
    topic: str | None = None,
    dimension: str | None = None,
) -> None:
    if not _allows(principal.allowed_tools, tool):
        raise MCPError("GEO_TOOL_NOT_ALLOWED", f"工具未授权: {tool}", {"tool": tool})
    if not principal.read_only:
        raise MCPError("GEO_SCOPE_DENIED", "当前 principal 未被标记为只读")
    if snapshot_id is not None and not principal.allowed_snapshot_ids:
        raise MCPError(
            "GEO_SCOPE_MAPPING_REQUIRED",
            "principal 尚未建立 GeoAgent snapshot scope 映射",
            {"geo_workspace_id": principal.geo_workspace_id},
        )
    if snapshot_id is not None and not _snapshot_allowed(principal, snapshot_id, working_snapshot_id=working_snapshot_id):
        raise MCPError("GEO_SCOPE_DENIED", "snapshot 不在当前 GeoAgent scope 内", {"snapshot_id": snapshot_id})
    if topic and not _allows(principal.allowed_topics, topic):
        raise MCPError("GEO_SCOPE_DENIED", f"topic 未授权: {topic}", {"topic": topic})
    if dimension and not _allows(principal.allowed_dimensions, dimension):
        raise MCPError("GEO_SCOPE_DENIED", f"dimension 未授权: {dimension}", {"dimension": dimension})
