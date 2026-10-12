"""Local credential authentication for the independent GeoAgent MCP server."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .contracts import MCPError, Principal


def _csv(value: str | None) -> frozenset[str]:
    return frozenset(item.strip() for item in (value or "").split(",") if item.strip())


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _scope_values(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        return _csv(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(item).strip() for item in value if str(item).strip())
    return frozenset()


def _principal_from_mapping(payload: dict[str, Any]) -> Principal:
    required = ("principal_id", "agent_id", "geo_workspace_id")
    missing = [key for key in required if not str(payload.get(key, "")).strip()]
    if missing:
        raise ValueError(f"scope principal 缺少字段: {', '.join(missing)}")
    return Principal(
        principal_id=str(payload["principal_id"]),
        agent_id=str(payload["agent_id"]),
        geo_workspace_id=str(payload["geo_workspace_id"]),
        allowed_snapshot_ids=_scope_values(payload.get("allowed_snapshot_ids")),
        allowed_topics=_scope_values(payload.get("allowed_topics")),
        allowed_dimensions=_scope_values(payload.get("allowed_dimensions")),
        allowed_tools=_scope_values(payload.get("allowed_tools")),
        read_only=bool(payload.get("read_only", True)),
    )


def _expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("scope credential expires_at 必须是 ISO-8601 时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


@dataclass(frozen=True)
class CredentialBinding:
    """A hash-only credential binding and its GeoAgent-owned principal scope."""

    credential_id: str
    token_hash: str
    principal: Principal
    expires_at: str | None = None
    revoked: bool = False


@dataclass(frozen=True)
class AuthConfig:
    token_hash: str | None
    principal: Principal | None
    bindings: tuple[CredentialBinding, ...] = ()

    @classmethod
    def from_env(cls) -> "AuthConfig":
        # The raw dev token is read from the process environment only; it is never
        # retained in the config or logged. Production should provide a hash or a
        # short-lived/OIDC credential through the deployment secret store.
        scope_file = os.getenv("GEOAGENT_MCP_SCOPE_FILE", "").strip()
        bindings = cls._load_scope_file(scope_file) if scope_file else ()
        raw_token = os.getenv("GEOAGENT_MCP_BEARER_TOKEN", "")
        host = os.getenv("GEOAGENT_MCP_HOST", "127.0.0.1").strip().lower()
        require_scope_file = os.getenv("GEOAGENT_MCP_REQUIRE_SCOPE_FILE", "false").lower() == "true"
        if require_scope_file and not bindings:
            raise ValueError("GEOAGENT_MCP_REQUIRE_SCOPE_FILE=true 时必须提供有效 scope 文件")
        if raw_token and host not in {"127.0.0.1", "localhost", "::1"} and not bindings:
            raise ValueError("非 loopback MCP host 禁止使用明文开发 Bearer token；请配置 scope 文件")
        if bindings and (raw_token or os.getenv("GEOAGENT_MCP_TOKEN_HASH", "").strip()):
            raise ValueError("scope 文件已配置时禁止同时配置 legacy Bearer token/hash，避免绕过 principal scope")
        token_hash = os.getenv("GEOAGENT_MCP_TOKEN_HASH", "").strip().lower() or (_hash_token(raw_token) if raw_token else None)
        allowed_snapshots = _csv(os.getenv("GEOAGENT_MCP_ALLOWED_SNAPSHOTS"))
        allowed_topics = _csv(os.getenv("GEOAGENT_MCP_ALLOWED_TOPICS"))
        allowed_dimensions = _csv(os.getenv("GEOAGENT_MCP_ALLOWED_DIMENSIONS"))
        # The local GeoAgent↔PiChat loop is intentionally open within the
        # loopback-only development boundary.  Without this default, an
        # otherwise valid local principal has no snapshot mapping and every
        # snapshot-scoped tool returns GEO_SCOPE_MAPPING_REQUIRED.  Explicit
        # values still win, and non-loopback deployments remain gated by the
        # scope-file requirement above.
        if host in {"127.0.0.1", "localhost", "::1"} and not bindings:
            allowed_snapshots = allowed_snapshots or frozenset({"*"})
            allowed_topics = allowed_topics or frozenset({"*"})
            allowed_dimensions = allowed_dimensions or frozenset({"*"})
        allowed_tools = _csv(os.getenv("GEOAGENT_MCP_ALLOWED_TOOLS"))
        principal = None
        if token_hash:
            principal = Principal(
                principal_id=os.getenv("GEOAGENT_MCP_PRINCIPAL_ID", "local-dev"),
                agent_id=os.getenv("GEOAGENT_MCP_AGENT_ID", "geoagent-local"),
                geo_workspace_id=os.getenv("GEOAGENT_MCP_WORKSPACE_ID", "local"),
                allowed_snapshot_ids=allowed_snapshots,
                allowed_topics=allowed_topics,
                allowed_dimensions=allowed_dimensions,
                allowed_tools=allowed_tools,
                read_only=os.getenv("GEOAGENT_MCP_READ_ONLY", "true").lower() != "false",
            )
        return cls(token_hash=token_hash, principal=principal, bindings=bindings)

    @staticmethod
    def _load_scope_file(scope_file: str) -> tuple[CredentialBinding, ...]:
        path = Path(scope_file)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"scope 文件不可读: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"scope 文件不是有效 JSON: {path}") from exc
        entries = payload.get("credentials") if isinstance(payload, dict) else None
        if not isinstance(entries, list) or not entries:
            raise ValueError("scope 文件必须包含非空 credentials 数组")
        result = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("scope credential 条目必须是对象")
            credential_id = str(entry.get("credential_id", "")).strip()
            token_hash = str(entry.get("token_hash", "")).strip().lower()
            principal_payload = entry.get("principal") if isinstance(entry.get("principal"), dict) else entry
            if not credential_id or len(token_hash) != 64:
                raise ValueError("scope credential 必须包含 credential_id 和 64 位 token_hash")
            principal = _principal_from_mapping(principal_payload)
            expires_at = str(entry.get("expires_at", "")).strip() or None
            _expired(expires_at)
            result.append(CredentialBinding(
                credential_id=credential_id,
                token_hash=token_hash,
                principal=principal,
                expires_at=expires_at,
                revoked=bool(entry.get("revoked", False)),
            ))
        return tuple(result)

    def authenticate(self, token: str | None) -> Principal:
        if not token:
            raise MCPError("GEO_AUTH_REQUIRED", "需要有效的 GeoAgent MCP Bearer credential")
        presented_hash = _hash_token(token)
        for binding in self.bindings:
            if hmac.compare_digest(presented_hash, binding.token_hash):
                if binding.revoked or _expired(binding.expires_at):
                    raise MCPError("GEO_AUTH_REQUIRED", "GeoAgent MCP credential 已过期或撤销")
                return binding.principal
        if self.token_hash and self.principal is not None and hmac.compare_digest(presented_hash, self.token_hash):
            return self.principal
        raise MCPError("GEO_AUTH_REQUIRED", "GeoAgent MCP credential 无效")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Protect only the MCP endpoint; health checks remain public."""

    def __init__(self, app: Callable[..., Awaitable[Response]], *, config: AuthConfig, mcp_path: str = "/mcp", managed_keys: bool = False) -> None:
        super().__init__(app)
        self.config = config
        self.managed_keys = managed_keys
        self.mcp_path = mcp_path.rstrip("/") or "/mcp"

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path.rstrip("/") != self.mcp_path:
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        try:
            credential = token if scheme.lower() == "bearer" else None
            principal = None
            if self.managed_keys:
                from backend.integrations import managed_principal
                principal = managed_principal(credential)
            if principal is None:
                principal = self.config.authenticate(credential)
        except MCPError as exc:
            return JSONResponse(
                {"ok": False, "error": {"code": exc.code, "message": exc.message}},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.scope.setdefault("state", {})["geo_principal"] = principal
        return await call_next(request)


def principal_from_request(request: Request) -> Principal:
    principal = getattr(request.state, "geo_principal", None)
    if not isinstance(principal, Principal):
        raise MCPError("GEO_AUTH_REQUIRED", "MCP request 未建立 GeoAgent principal")
    return principal
