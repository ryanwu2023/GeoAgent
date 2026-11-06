"""Browser login with revocable PostgreSQL sessions.

Accounts in the mounted htpasswd file are administrators; administrators create
ordinary users stored in PostgreSQL, each mirrored as a PiChat embed shadow user.
MCP deliberately retains its independent Bearer authentication.
"""
import hashlib
import hmac
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from passlib.hash import sha512_crypt
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from .database import connect
from .integrations import embed_identity_payload, pichat_tls_context, settings as integration_settings

router = APIRouter(prefix="/api/auth")
COOKIE = "geoagent_session"
TTL = 12 * 3600
PUBLIC = {"/login", "/healthz", "/readyz", "/api/auth/session", "/api/auth/login", "/api/auth/logout"}
# Same SHA512-crypt cost as the htpasswd file; never store plaintext passwords.
HASHER = sha512_crypt.using(rounds=5000)
# Use the same cost for unknown accounts.
DUMMY = HASHER.hash("not-an-account-" + secrets.token_hex(16))
USERNAME = re.compile(r"[A-Za-z0-9_.@-]{2,64}")


def enabled():
    return os.getenv("GEO_WEB_AUTH_ENABLED", "false").lower() == "true"


def secure_cookie():
    return os.getenv("GEO_WEB_AUTH_SECURE_COOKIE", "true").lower() == "true"


def users():
    path = Path(os.getenv("GEO_WEB_AUTH_FILE", "/etc/geoagent-auth/auth"))
    try:
        result = {}
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            name, password_hash = line.split(":", 1)
            # Existing Sealos secret uses SHA512-crypt. Unsupported/corrupt
            # entries fail closed rather than silently dropping an account.
            sha512_crypt.from_string(password_hash)
            if not name or name in result:
                raise ValueError("Invalid account")
            result[name] = password_hash
        if not result:
            raise ValueError("Empty account file")
        return result
    except (OSError, ValueError):
        raise HTTPException(503, "登录配置暂不可用，请联系管理员") from None


def account(username):
    """Return (password_hash, is_admin) for an active account, else None."""
    admins = users()
    if username in admins:
        return admins[username], True
    with connect() as conn:
        row = conn.execute("SELECT password_hash FROM web_users WHERE username=%s AND NOT is_deleted", (username,)).fetchone()
    return (row["password_hash"], False) if row else None


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def session_user(request):
    token = request.cookies.get(COOKIE, "")
    if len(token) != 64:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT username,credential_hash FROM web_sessions WHERE token_hash=%s AND expires_at > CURRENT_TIMESTAMP",
            (fingerprint(token),),
        ).fetchone()
    current = account(row["username"]) if row else None
    if current and hmac.compare_digest(row["credential_hash"], fingerprint(current[0])):
        return row["username"]
    return None


def check_origin(request):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请从本站页面提交请求")
    if origin:
        parsed = urlsplit(origin)
        allowed = {x.strip().rstrip("/") for x in os.getenv("GEOAGENT_ALLOWED_ORIGINS", "").split(",") if x.strip()}
        # Production uses explicit HTTPS origins behind the TLS ingress.
        valid = origin in allowed if allowed else parsed.scheme in {"http", "https"} and parsed.netloc == request.headers.get("host")
        if not valid:
            raise HTTPException(403, "请从本站页面提交请求")


class WebAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if not enabled() or path.rstrip("/") == "/mcp":
            return await call_next(request)
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                check_origin(request)
            public = path in PUBLIC or path.startswith("/assets/") or path == "/favicon.ico"
            if not public:
                username = await run_in_threadpool(session_user, request)
                if not username:
                    if path.startswith("/api/") or request.method not in {"GET", "HEAD"}:
                        return JSONResponse({"detail": "登录已过期，请重新登录", "code": "LOGIN_REQUIRED"}, 401, headers={"Cache-Control": "no-store"})
                    return RedirectResponse("/login", 303, headers={"Cache-Control": "no-store"})
                request.state.web_user = username
            response = await call_next(request)
            if not path.startswith("/assets/"):
                response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, exc.status_code, headers={"Cache-Control": "no-store"})


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


@router.get("/session")
def session(request: Request):
    if not enabled():
        return {"enabled": False, "authenticated": True, "username": ""}
    users()  # Configuration errors must not look like an ordinary logged-out state.
    username = session_user(request)
    return {"enabled": True, "authenticated": bool(username), "username": username or "", "is_admin": bool(username) and username in users()}


def auth_action(request):
    if not enabled():
        raise HTTPException(404, "未启用网页登录")
    check_origin(request)
    if request.headers.get("x-geoagent-login") != "1":
        raise HTTPException(403, "请使用登录页面")


@router.post("/login")
def login(body: Login, request: Request):
    auth_action(request)
    users()  # A broken admin file fails closed before any attempt is counted.
    # Shared rate limit survives process restarts and covers all app workers.
    bucket = int(time.time()) // 300
    limited = False
    with connect() as conn:
        conn.execute("DELETE FROM web_login_attempts WHERE bucket < %s", (bucket - 1,))
        for key, limit in (("global", 60), ("user:" + fingerprint(body.username), 10)):
            count = conn.execute(
                "INSERT INTO web_login_attempts (key,bucket,attempts) VALUES (%s,%s,1) ON CONFLICT (key,bucket) DO UPDATE SET attempts=web_login_attempts.attempts+1 RETURNING attempts",
                (key, bucket),
            ).fetchone()[0]
            limited |= count > limit
    if limited:
        raise HTTPException(429, "登录尝试过于频繁，请五分钟后重试", headers={"Retry-After": "300"})
    found = account(body.username)
    password_hash = found[0] if found else DUMMY
    correct = sha512_crypt.verify(body.password, password_hash)
    if not correct or not found:
        raise HTTPException(401, "账号或密码不正确")
    token = secrets.token_hex(32)
    with connect() as conn:
        conn.execute("DELETE FROM web_sessions WHERE expires_at <= CURRENT_TIMESTAMP OR token_hash=%s", (fingerprint(request.cookies.get(COOKIE, "")),))
        conn.execute(
            "INSERT INTO web_sessions (token_hash,username,credential_hash,expires_at) VALUES (%s,%s,%s,CURRENT_TIMESTAMP + %s * INTERVAL '1 second')",
            (fingerprint(token), body.username, fingerprint(password_hash), TTL),
        )
    response = JSONResponse({"enabled": True, "authenticated": True, "username": body.username, "is_admin": found[1]})
    response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, secure=secure_cookie(), samesite="lax", path="/")
    return response


@router.post("/logout")
def logout(request: Request):
    auth_action(request)
    with connect() as conn:
        conn.execute("DELETE FROM web_sessions WHERE token_hash=%s", (fingerprint(request.cookies.get(COOKIE, "")),))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=secure_cookie(), samesite="lax")
    return response


class NewUser(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    # Empty password means the default: the same as the username.
    password: str = Field(default="", max_length=1024)
    # Empty phone means a generated placeholder; PiChat requires one per user.
    phone: str = Field(default="", max_length=40)


def pichat_phone(raw, product_user_id):
    """E.164 phone for PiChat, normalized the same way PiChat does."""
    if not raw.strip():
        # +999 is unassigned, so placeholders can never collide with real numbers.
        return f"+999{int(hashlib.sha256(product_user_id.encode()).hexdigest(), 16) % 10**11:011d}"
    compact = re.sub(r"[\s\-()]", "", raw)
    mainland = re.fullmatch(r"(?:\+86|86)?(1[3-9]\d{9})", compact)
    if mainland:
        return "+86" + mainland[1]
    if compact.startswith("+86") or not re.fullmatch(r"\+[1-9]\d{7,14}", compact):
        raise HTTPException(422, "手机号格式无效，请填写大陆手机号或 E.164 号码（如 +85251234567）")
    return compact


def pichat_subject(username):
    """(productUserId, phone, displayName) of a GeoAgent-created user, else None."""
    if not username:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT product_user_id,phone FROM web_users WHERE username=%s AND NOT is_deleted", (username,)
        ).fetchone()
    return (row["product_user_id"], row["phone"], username) if row else None


def provision_pichat(product_user_id, phone, display_name):
    """Materialize the PiChat shadow user now; any failure aborts user creation."""
    cfg = integration_settings()
    if not cfg["app_key"]:
        raise HTTPException(503, "未配置 PiChat 应用 Key，无法在 PiChat 同步创建用户")
    try:
        with httpx.Client(timeout=10, verify=pichat_tls_context()) as client:
            response = client.post(
                f"{cfg['origin'].rstrip('/')}/api/v1/embed/identity",
                headers={"Authorization": f"Bearer {cfg['app_key']}"},
                json=embed_identity_payload(cfg, product_user_id, phone, display_name),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"PiChat 不可用，用户未创建：{type(exc).__name__}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    data = body.get("data") if isinstance(body, dict) else None
    if response.status_code < 400 and body.get("ok") and isinstance(data, dict) and data.get("userId"):
        return str(data["userId"])
    error = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
    # Credential failures never echo upstream details.
    detail = "应用 Key 被拒绝或无权限" if response.status_code in (401, 403) else str(error.get("message") or "未返回有效身份")[:200]
    raise HTTPException(502, f"PiChat 创建用户失败（HTTP {response.status_code}）：{detail}，用户未创建")


class PasswordReset(BaseModel):
    password: str = Field(default="", max_length=1024)


def admin(request, mutation=True):
    """Only htpasswd-file accounts may manage users; no old password is needed."""
    if not enabled():
        raise HTTPException(404, "未启用网页登录")
    if mutation:
        auth_action(request)
    username = getattr(request.state, "web_user", "")
    if username not in users():
        raise HTTPException(403, "仅管理员可以管理用户")
    return username


def drop_sessions(conn, username):
    # Credential checks already reject stale sessions; delete them eagerly too.
    conn.execute("DELETE FROM web_sessions WHERE username=%s", (username,))


@router.get("/users")
def list_users(request: Request):
    admin(request, mutation=False)
    with connect() as conn:
        rows = conn.execute(
            "SELECT username,phone,pichat_user_id,created_by,created_at,updated_by,updated_at FROM web_users WHERE NOT is_deleted ORDER BY created_at,id"
        ).fetchall()
    return {
        "admins": sorted(users()),
        "users": [{**row, "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"] and row["updated_at"].isoformat()} for row in rows],
    }


@router.post("/users", status_code=201)
def create_user(body: NewUser, request: Request):
    operator = admin(request)
    if not USERNAME.fullmatch(body.username):
        raise HTTPException(422, "账号只能包含 2-64 位字母、数字或 _.@-")
    if body.username in users():
        raise HTTPException(409, "该账号已存在")
    product_user_id = "geo-" + secrets.token_hex(16)
    phone = pichat_phone(body.phone, product_user_id)
    # The row stays uncommitted (and its name and phone locked) until PiChat
    # confirms, so both systems either have the user or neither does.
    with connect() as conn:
        try:
            created = conn.execute(
                "INSERT INTO web_users (username,password_hash,product_user_id,phone,created_by) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (username) WHERE NOT is_deleted DO NOTHING RETURNING id",
                (body.username, HASHER.hash(body.password or body.username), product_user_id, phone, operator),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "该手机号已被其他用户使用") from None
        if not created:
            raise HTTPException(409, "该账号已存在")
        pichat_user_id = provision_pichat(product_user_id, phone, body.username)
        conn.execute("UPDATE web_users SET pichat_user_id=%s WHERE id=%s", (pichat_user_id, created["id"]))
    return {"username": body.username, "default_password": not body.password, "phone_generated": not body.phone.strip(), "pichat_user_id": pichat_user_id}


@router.put("/users/{username}/password")
def reset_password(username: str, body: PasswordReset, request: Request):
    operator = admin(request)
    if username in users():
        raise HTTPException(409, "管理员账号由部署配置管理，不能在页面修改")
    with connect() as conn:
        changed = conn.execute(
            "UPDATE web_users SET password_hash=%s,updated_by=%s,updated_at=CURRENT_TIMESTAMP WHERE username=%s AND NOT is_deleted RETURNING username",
            (HASHER.hash(body.password or username), operator, username),
        ).fetchone()
        if not changed:
            raise HTTPException(404, "用户不存在")
        drop_sessions(conn, username)
    return {"username": username, "default_password": not body.password}


@router.delete("/users/{username}")
def delete_user(username: str, request: Request):
    operator = admin(request)
    if username in users():
        raise HTTPException(409, "管理员账号由部署配置管理，不能在页面删除")
    with connect() as conn:
        deleted = conn.execute(
            "UPDATE web_users SET is_deleted=true,updated_by=%s,updated_at=CURRENT_TIMESTAMP WHERE username=%s AND NOT is_deleted RETURNING username",
            (operator, username),
        ).fetchone()
        if not deleted:
            raise HTTPException(404, "用户不存在")
        drop_sessions(conn, username)
    return {"ok": True}
