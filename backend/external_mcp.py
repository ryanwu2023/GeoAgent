"""Persistent registry for external MCP servers.

Secrets are stored only in the ignored data directory and are never included in
the public projection returned to the browser.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from .mcp_news import McpClient
from .store import data_dir

REGISTRY_VERSION = 1
NEWS_REQUIRED_TOOLS = ("service_status", "knowledge_search")


def news_import_capability(tools: list | None) -> dict:
    """Tool discovery proves the news contract, not source freshness/availability."""
    names = {
        str(item.get("name", "")) if isinstance(item, dict) else str(item)
        for item in tools or []
    }
    missing = [name for name in NEWS_REQUIRED_TOOLS if name not in names]
    return {
        "supported": None if tools is None else not missing,
        "required_tools": list(NEWS_REQUIRED_TOOLS),
        "missing_tools": [] if tools is None else missing,
    }


def _registry_path() -> Path:
    return data_dir() / "mcp-servers.json"


def _legacy_path() -> Path:
    return data_dir() / "mcp-config.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return slug[:64] or "mcp-server"


def _normalise_key(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.lower().startswith("bearer "):
        token = value[7:].strip()
        return f"Bearer {token}" if token else ""
    return f"Bearer {value}"


def _safe_error(exc: Exception, authorization: str) -> str:
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        reason = getattr(response, "reason_phrase", "")
        return f"目标服务返回 HTTP {response.status_code}{f' {reason}' if reason else ''}"
    message = str(exc)
    if authorization:
        message = message.replace(authorization, "***")
    token = authorization.removeprefix("Bearer ")
    if token:
        message = message.replace(token, "***")
    return message[:240] or type(exc).__name__


def _validate_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("MCP 地址必须是无内嵌凭据、查询参数或片段的 HTTP/HTTPS URL")
    return value


def _public(server: dict) -> dict:
    last_test = server.get("last_test") if isinstance(server.get("last_test"), dict) else None
    return {
        "id": server.get("id", ""),
        "name": server.get("name", ""),
        "type": server.get("type", "http"),
        "url": server.get("url", ""),
        "configured": bool(server.get("url") and server.get("authorization")),
        "verify_tls": bool(server.get("verify_tls", True)),
        "news_import": (
            last_test["news_import"]
            if last_test and isinstance(last_test.get("news_import"), dict)
            else news_import_capability(None)
        ),
        "created_at": server.get("created_at"),
        "updated_at": server.get("updated_at"),
        "last_test": last_test,
    }


def _write_registry(registry: dict) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _migrate_legacy() -> dict:
    # Persist even an empty registry, so a deleted connection is never resurrected
    # by a later read of the old file. The legacy file is a retained backup only.
    registry = {"version": REGISTRY_VERSION, "servers": []}
    legacy_path = _legacy_path()
    if not legacy_path.is_file():
        _write_registry(registry)
        return registry
    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        _write_registry(registry)
        return registry
    if not isinstance(legacy, dict) or not legacy.get("url") or not legacy.get("authorization"):
        _write_registry(registry)
        return registry
    # Do not migrate unsafe URLs (credentials/query strings must never be public).
    _validate_url(str(legacy["url"]))
    stamp = _now()
    server = {
        "id": _slug(str(legacy.get("name") or "mcp-news")),
        "name": str(legacy.get("name") or "mcp-news")[:80],
        "type": "http",
        "url": str(legacy["url"]),
        "authorization": str(legacy["authorization"]),
        "verify_tls": bool(legacy.get("verify_tls", True)),
        "created_at": stamp,
        "updated_at": stamp,
        "last_test": None,
    }
    registry = {"version": REGISTRY_VERSION, "servers": [server]}
    _write_registry(registry)
    return registry


def load_registry() -> dict:
    path = _registry_path()
    if not path.is_file():
        return _migrate_legacy()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("外部 MCP 服务注册表无法读取") from exc
    if not isinstance(payload, dict):
        raise ValueError("外部 MCP 服务注册表格式无效")
    servers = payload.get("servers")
    if not isinstance(servers, list):
        raise ValueError("外部 MCP 服务注册表格式无效")
    return {"version": REGISTRY_VERSION, "servers": [item for item in servers if isinstance(item, dict)]}


def public_registry() -> dict:
    registry = load_registry()
    return {"version": registry["version"], "servers": [_public(item) for item in registry["servers"]]}


def _existing_server(registry: dict, server_id: str | None) -> dict | None:
    if not server_id:
        return None
    return next((item for item in registry["servers"] if item.get("id") == server_id), None)


def resolve_server(server_id: str | None = None) -> dict | None:
    """Resolve explicitly, or retain the former single-connection default."""
    registry = load_registry()
    if server_id:
        server = _existing_server(registry, server_id)
        if server is None:
            raise ValueError("所选外部 MCP 服务不存在，请重新选择")
        return dict(server)
    if len(registry["servers"]) > 1:
        raise ValueError("已配置多个外部 MCP 服务，请明确选择 server_id")
    return dict(registry["servers"][0]) if registry["servers"] else None


def prepare_draft(
    server_id: str | None, name: str, transport: str, url: str, key: str,
    verify_tls: bool | None = None,
) -> tuple[dict, dict]:
    registry = load_registry()
    existing = _existing_server(registry, server_id)
    if server_id and existing is None:
        raise ValueError("要编辑的 MCP 服务不存在")
    name = name.strip()
    if not name:
        raise ValueError("请填写 MCP 服务名称")
    if len(name) > 80:
        raise ValueError("MCP 服务名称不能超过 80 个字符")
    if transport != "http":
        raise ValueError("当前仅支持 HTTP 类型的 MCP 服务")
    if any(item.get("name", "").casefold() == name.casefold() and item.get("id") != server_id for item in registry["servers"]):
        raise ValueError("MCP 服务名称已存在")
    authorization = _normalise_key(key) or (str(existing.get("authorization", "")) if existing else "")
    if not authorization:
        raise ValueError("新增 MCP 服务时必须填写 Key")
    stamp = _now()
    candidate_id = str(existing.get("id")) if existing else _slug(name)
    if not existing:
        used = {str(item.get("id")) for item in registry["servers"]}
        base = candidate_id
        suffix = 2
        while candidate_id in used:
            candidate_id = f"{base[:58]}-{suffix}"
            suffix += 1
    draft = {
        "id": candidate_id,
        "name": name,
        "type": transport,
        "url": _validate_url(url),
        "authorization": authorization,
        "verify_tls": (
            bool(verify_tls) if verify_tls is not None
            else bool(existing.get("verify_tls", True)) if existing else True
        ),
        "created_at": existing.get("created_at") if existing else stamp,
        "updated_at": stamp,
    }
    return registry, draft


async def test_and_save(
    server_id: str | None, name: str, transport: str, url: str, key: str,
    verify_tls: bool | None = None,
) -> dict:
    registry, draft = prepare_draft(server_id, name, transport, url, key, verify_tls)
    started = perf_counter()
    client = McpClient(draft)
    try:
        server_info = await client.connect()
    except Exception as exc:
        raise ValueError(f"initialize 阶段失败：{_safe_error(exc, draft['authorization'])}") from exc
    try:
        tools = await client.list_tools()
    except Exception as exc:
        raise ValueError(f"tools/list 阶段失败：{_safe_error(exc, draft['authorization'])}") from exc
    if not isinstance(tools, list):
        raise ValueError("MCP tools/list 未返回工具数组")
    duration_ms = round((perf_counter() - started) * 1000)
    names = [str(item.get("name")) for item in tools if isinstance(item, dict) and item.get("name")]
    draft["last_test"] = {
        "ok": True,
        "tested_at": _now(),
        "duration_ms": duration_ms,
        "tool_count": len(tools),
        "tool_names": names[:20],
        "news_import": news_import_capability(names),
    }
    if server_id:
        registry["servers"] = [draft if item.get("id") == server_id else item for item in registry["servers"]]
    else:
        registry["servers"].append(draft)
    _write_registry(registry)
    return {
        "ok": True,
        "server": _public(draft),
        "server_info": server_info,
        "tools": names,
    }


def delete_server(server_id: str) -> dict:
    registry = load_registry()
    remaining = [item for item in registry["servers"] if item.get("id") != server_id]
    if len(remaining) == len(registry["servers"]):
        raise KeyError(server_id)
    registry["servers"] = remaining
    _write_registry(registry)
    return {"version": registry["version"], "servers": [_public(item) for item in remaining]}
