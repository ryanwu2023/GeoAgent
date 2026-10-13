"""Persisted runtime integration settings and hash-only MCP credentials."""
import hashlib
import json
import os
import secrets
import ssl
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .store import db

def pichat_tls_context():
    """Explicit internal CA compatibility; still verify chain and hostname."""
    ca_file = os.getenv('GEOAGENT_PICHAT_CA_FILE', '')
    if not ca_file:
        return True
    context = ssl.create_default_context(cafile=ca_file)
    # Older internal roots omit KU; OpenSSL standard verification accepts them.
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context

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
    cfg = {key: saved.get(key, os.getenv(env, default)) for key, (env, default) in FIELDS.items()}
    # Local acceptance overrides never reuse another instance's credential.
    # The persisted configuration is untouched and returns after restart.
    local_origin = os.getenv('GEOAGENT_LOCAL_PICHAT_ORIGIN', '')
    if local_origin:
        origin = normalize_origin(local_origin)
        if urlsplit(origin).hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise HTTPException(422, '本地联调覆盖仅允许回环地址')
        cfg['origin'] = origin
        cfg['app_key'] = os.getenv('GEOAGENT_LOCAL_PICHAT_APP_KEY', '')
    return cfg


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
    """No separate management password; deployment owns site access control.

    Retain same-origin protection. Public MCP Bearer authentication is separate.
    The name is retained for existing router dependency imports.
    """
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
    if os.getenv('GEOAGENT_LOCAL_PICHAT_ORIGIN', ''):
        raise HTTPException(409, '本地联调覆盖已启用，原配置保留只读；取消覆盖并重启后再保存')
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
    if "deleted_at" not in {row[1] for row in conn.execute("PRAGMA table_info(integration_mcp_keys)")}:
        # StrictMode or multiple tabs can race on the first old-schema load.
        # Serialize migration, then re-check after acquiring the writer lock.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        if "deleted_at" not in {row[1] for row in conn.execute("PRAGMA table_info(integration_mcp_keys)")}:
            conn.execute("ALTER TABLE integration_mcp_keys ADD COLUMN deleted_at TEXT")


@router.get("/mcp-keys", dependencies=[Depends(admin)])
def keys():
    with db() as conn:
        key_table(conn)
        return [dict(row) for row in conn.execute("SELECT id,label,created_at,revoked_at FROM integration_mcp_keys WHERE deleted_at IS NULL ORDER BY created_at DESC")]


class NewKey(BaseModel):
    label: str = Field(min_length=1, max_length=100)


@router.post("/mcp-keys", dependencies=[Depends(admin)])
def create_key(body: NewKey):
    raw = "geo_" + secrets.token_urlsafe(32)
    key_id = secrets.token_hex(8)
    created = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        key_table(conn)
        conn.execute("INSERT INTO integration_mcp_keys(id,label,token_hash,created_at,revoked_at) VALUES(?,?,?,?,NULL)",
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


@router.delete("/mcp-keys/{key_id}/record", dependencies=[Depends(admin)])
def delete_revoked_key(key_id: str):
    # Retain a revoked tombstone, never restore the credential through cleanup.
    with db() as conn:
        key_table(conn)
        row = conn.execute("SELECT revoked_at FROM integration_mcp_keys WHERE id=?", (key_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Key 不存在")
        if not row["revoked_at"]:
            raise HTTPException(409, "启用中的 Key 不可删除，请先撤销")
        conn.execute("UPDATE integration_mcp_keys SET deleted_at=? WHERE id=?",
                     (datetime.now(timezone.utc).isoformat(), key_id))
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
