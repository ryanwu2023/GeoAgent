"""Persisted runtime integration settings and hash-only MCP credentials."""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .store import db

def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/integrations", dependencies=[Depends(no_cache)])
FIELDS = {"origin": ("PICHAT_EMBED_BASE_URL", "http://127.0.0.1:9172"),
          "agent_id": ("PICHAT_AGENT_ID", "geo-research-assistant"),
          "app_key": ("PICHAT_EMBED_APP_KEY", ""), "phone": ("PICHAT_EMBED_PHONE", ""),
          "entry": ("PICHAT_EMBED_ENTRY", ""),
          "product_user_id": ("PICHAT_EMBED_PRODUCT_USER_ID", "geoagent-local")}


def settings():
    with db() as conn:
        row = conn.execute("SELECT value FROM workspace_state WHERE key='integrations.pichat'").fetchone()
    saved = json.loads(row["value"]) if row else {}
    return {key: saved.get(key, os.getenv(env, default)) for key, (env, default) in FIELDS.items()}


def normalize_origin(value):
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
    except ValueError as exc:
        raise HTTPException(422, "PiChat 服务地址格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(422, "PiChat 地址必须是无用户名、密码的 HTTP(S) 地址")
    if any(char.isspace() for char in parsed.netloc):
        raise HTTPException(422, "PiChat 服务地址不能包含空白")
    if parsed.path.rstrip("/") not in {"", "/chat"} or parsed.query or parsed.fragment:
        raise HTTPException(422, "请输入 PiChat 根地址或 /chat 地址")
    return f"{parsed.scheme}://{parsed.netloc}"


def admin(request: Request):
    host = request.client.host if request.client else ""
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    # Opt-in delegation to the isolated, loopback Nginx sidecar. The gateway
    # overwrites this header only after its existing Basic login succeeds.
    trusted_gateway = (os.getenv("GEOAGENT_TRUST_AUTHENTICATED_GATEWAY", "false").lower() == "true"
                       and local and bool(request.headers.get("X-GeoAgent-Gateway-User")))
    expected = os.getenv("GEOAGENT_SETTINGS_TOKEN", "")
    presented = request.headers.get("X-GeoAgent-Admin", "")
    if trusted_gateway:
        pass
    elif expected:
        if not hmac.compare_digest(expected, presented):
            raise HTTPException(403, "请输入项目管理口令")
    else:
        # A local reverse proxy must not make a public caller a local admin.
        forwarded = request.headers.get("forwarded") or request.headers.get("x-forwarded-for")
        if not local or forwarded or request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            raise HTTPException(403, "公网配置需先设置 GEOAGENT_SETTINGS_TOKEN")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "禁止跨站管理请求")


def public_settings():
    cfg = settings()
    return {"origin": cfg["origin"], "agent_id": cfg["agent_id"], "product_user_id": cfg["product_user_id"]}


@router.get("/public")
def public():
    return public_settings()


@router.get("", dependencies=[Depends(admin)])
def read():
    cfg = settings()
    return {**{k: v for k, v in cfg.items() if k != "app_key"},
            "app_key_configured": bool(cfg["app_key"]), "mcp_path": "/mcp"}


class PiChatSettings(BaseModel):
    origin: str = Field(max_length=500)
    agent_id: str = Field(min_length=1, max_length=120)
    app_key: str = Field(default="", max_length=500)
    phone: str = Field(max_length=40)
    entry: str = Field(default="", max_length=120)
    product_user_id: str = Field(default="geoagent-local", min_length=1, max_length=120)


@router.put("/pichat", dependencies=[Depends(admin)])
def save(body: PiChatSettings):
    cfg = body.model_dump()
    cfg["origin"] = normalize_origin(cfg["origin"])
    cfg = {k: v.strip() for k, v in cfg.items()}
    if not cfg["app_key"]:
        cfg["app_key"] = settings()["app_key"]
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO workspace_state(key,value,updated_at) VALUES(?,?,?)",
                     ("integrations.pichat", json.dumps(cfg), datetime.now(timezone.utc).isoformat()))
    return read()


def key_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS integration_mcp_keys (id TEXT PRIMARY KEY, label TEXT NOT NULL, token_hash TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT)")


@router.get("/mcp-keys", dependencies=[Depends(admin)])
def keys():
    with db() as conn:
        key_table(conn)
        return [dict(row) for row in conn.execute("SELECT id,label,created_at,revoked_at FROM integration_mcp_keys ORDER BY created_at DESC")]


class NewKey(BaseModel):
    label: str = Field(min_length=1, max_length=100)


@router.post("/mcp-keys", dependencies=[Depends(admin)])
def create_key(body: NewKey):
    raw = "geo_" + secrets.token_urlsafe(32)
    key_id = secrets.token_hex(8)
    created = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        key_table(conn)
        conn.execute("INSERT INTO integration_mcp_keys VALUES(?,?,?,?,NULL)",
                     (key_id, body.label, hashlib.sha256(raw.encode()).hexdigest(), created))
    return {"id": key_id, "label": body.label, "created_at": created, "key": raw}


@router.delete("/mcp-keys/{key_id}", dependencies=[Depends(admin)])
def revoke_key(key_id: str):
    with db() as conn:
        key_table(conn)
        result = conn.execute("UPDATE integration_mcp_keys SET revoked_at=COALESCE(revoked_at,?) WHERE id=?",
                              (datetime.now(timezone.utc).isoformat(), key_id))
        if not result.rowcount:
            raise HTTPException(404, "Key 不存在")
    return {"ok": True}


def managed_principal(token):
    if not token or not token.startswith("geo_"):
        return None
    with db() as conn:
        key_table(conn)
        row = conn.execute("SELECT id FROM integration_mcp_keys WHERE token_hash=? AND revoked_at IS NULL",
                           (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row:
        return None
    from .geo_mcp.contracts import Principal
    scope = frozenset({"*"})
    return Principal(row["id"], "external-agent", "project", scope, scope, scope, scope, True)
