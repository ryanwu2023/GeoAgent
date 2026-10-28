"""Browser login with revocable PostgreSQL sessions and existing htpasswd users.

MCP deliberately retains its independent Bearer authentication.
"""
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from passlib.hash import sha512_crypt
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from .database import connect

router = APIRouter(prefix="/api/auth")
COOKIE = "geoagent_session"
TTL = 12 * 3600
PUBLIC = {"/login", "/healthz", "/readyz", "/api/auth/session", "/api/auth/login", "/api/auth/logout"}
# Use the same cost for unknown accounts; never store plaintext passwords.
DUMMY = sha512_crypt.using(rounds=5000).hash("not-an-account-" + secrets.token_hex(16))


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
    if row and hmac.compare_digest(row["credential_hash"], fingerprint(users().get(row["username"], ""))):
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
    return {"enabled": True, "authenticated": bool(username), "username": username or ""}


def auth_action(request):
    if not enabled():
        raise HTTPException(404, "未启用网页登录")
    check_origin(request)
    if request.headers.get("x-geoagent-login") != "1":
        raise HTTPException(403, "请使用登录页面")


@router.post("/login")
def login(body: Login, request: Request):
    auth_action(request)
    accounts = users()
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
    password_hash = accounts.get(body.username, DUMMY)
    correct = sha512_crypt.verify(body.password, password_hash)
    if not correct or body.username not in accounts:
        raise HTTPException(401, "账号或密码不正确")
    token = secrets.token_hex(32)
    with connect() as conn:
        conn.execute("DELETE FROM web_sessions WHERE expires_at <= CURRENT_TIMESTAMP OR token_hash=%s", (fingerprint(request.cookies.get(COOKIE, "")),))
        conn.execute(
            "INSERT INTO web_sessions (token_hash,username,credential_hash,expires_at) VALUES (%s,%s,%s,CURRENT_TIMESTAMP + %s * INTERVAL '1 second')",
            (fingerprint(token), body.username, fingerprint(password_hash), TTL),
        )
    response = JSONResponse({"enabled": True, "authenticated": True, "username": body.username})
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
