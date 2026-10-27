"""Hot-readable backend model settings; secrets never enter public responses."""
from __future__ import annotations

import ipaddress
import json
import math
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .store import ROOT, data_dir

PROVIDERS = {"disabled", "deepseek", "openai-compatible", "pichat"}
ENV_KEYS = (
    "PICHAT_BASE_URL", "PICHAT_API_KEY", "PICHAT_TIMEOUT",
    "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "LLM_TIMEOUT",
    "LLM_FAST_MODEL", "LLM_REASONING_MODEL", "LLM_AGENT_MODE",
)
PUBLIC_KEYS = (
    "provider", "base_url", "model", "timeout_seconds",
    "api_key_configured", "configured", "source", "protocol",
)


def settings_path() -> Path:
    return data_dir() / "model-settings.json"


def _text(value, name, *, maximum=2000):
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须为文本")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} 格式无效")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"{name} 格式无效")
    return value


def normalize_base_url(value, provider, *, allow_legacy_http=False):
    value = _text(value, "服务地址", maximum=1000)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("服务地址格式无效") from exc
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or "?" in value or "#" in value
        or "\\" in value or any(char.isspace() for char in value)
        or (port is not None and port < 1)
    ):
        raise ValueError("服务地址需为不含凭据、空白、查询参数或片段的 HTTP(S) API 地址")
    host = parsed.hostname.lower()
    if "%" in host:
        raise ValueError("服务地址主机格式无效")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if parsed.scheme == "http" and not loopback and not allow_legacy_http:
        raise ValueError("非本机回环的模型服务必须使用 HTTPS，避免明文发送凭据")
    path = parsed.path.rstrip("/")
    if "%" in path or any(part in {".", ".."} for part in path.split("/")):
        raise ValueError("请输入明确的 API 基址，不使用编码或相对路径")
    if provider == "pichat":
        if path not in {"", "/api/v1", "/api/v1/chat"}:
            raise ValueError("PiChat 后台调用需填写根地址或 /api/v1/chat，不能填写聊天网页地址")
        path = ""
    else:
        if path.endswith("/chat/completions"):
            path = path.removesuffix("/chat/completions")
        if any(part in {"chat", "admin"} for part in path.split("/")):
            raise ValueError("请输入模型 API 基址，不是聊天网页或管理页面地址")
    host = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host + (f":{port}" if port and port != default_port else "")
    return f"{parsed.scheme}://{authority}{path}"


def endpoint_for(settings):
    base = settings["base_url"]
    return base + ("/api/v1/chat" if settings["provider"] == "pichat" else "/chat/completions")


def _timeout(value, *, legacy=False):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
    except (TypeError, ValueError):
        if legacy:
            return 90.0
        raise ValueError("超时秒数须为 5 到 300 之间的数值") from None
    if legacy:
        return max(5.0, min(300.0, result))
    if not 5 <= result <= 300:
        raise ValueError("超时秒数须为 5 到 300 之间的数值")
    return result


def validate_settings(payload, *, source="saved", allow_auto_model=False, allow_legacy_http=False):
    """Validate a complete private configuration without inheriting any key."""
    if not isinstance(payload, dict):
        raise ValueError("后台模型配置必须为对象")
    provider = payload.get("provider", "disabled")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("后台模型提供方无效")
    timeout = _timeout(payload.get("timeout_seconds", 90), legacy=allow_auto_model)
    if provider == "disabled":
        return {
            "provider": "disabled", "base_url": "", "model": "",
            "timeout_seconds": timeout, "api_key": "", "source": source,
        }
    base = normalize_base_url(payload.get("base_url", ""), provider, allow_legacy_http=allow_legacy_http)
    model = "" if provider == "pichat" else _text(payload.get("model", ""), "模型", maximum=200)
    if provider != "pichat" and not model and not allow_auto_model:
        raise ValueError("请明确填写模型名称")
    key = _text(payload.get("api_key", ""), "API Key")
    if any(char.isspace() for char in key):
        raise ValueError("API Key 不能包含空白")
    return {
        "provider": provider, "base_url": base, "model": model,
        "timeout_seconds": timeout, "api_key": key, "source": source,
    }


def _environment_settings(env_root=None):
    values = {key: os.getenv(key, "") for key in ENV_KEYS}
    path = Path(env_root or ROOT) / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if key.strip() in values:
                    values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ENV_KEYS:
        if os.getenv(key):
            values[key] = os.environ[key]
    if values["PICHAT_API_KEY"]:
        provider = "pichat"
    elif any(values[key] for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")):
        provider = "openai-compatible"
    elif values["PICHAT_BASE_URL"]:
        provider = "pichat"
    else:
        return validate_settings({"provider": "disabled"}, source="none")
    pichat = provider == "pichat"
    return validate_settings({
        "provider": provider,
        "base_url": (
            values["PICHAT_BASE_URL"] or "http://127.0.0.1:9172"
            if pichat else values["LLM_BASE_URL"] or "https://api.openai.com/v1"
        ),
        "model": values["LLM_MODEL"] if not pichat else "",
        "api_key": values["PICHAT_API_KEY"] if pichat else values["LLM_API_KEY"],
        "timeout_seconds": (values["PICHAT_TIMEOUT"] if pichat else values["LLM_TIMEOUT"]) or 90,
    }, source="environment", allow_auto_model=True, allow_legacy_http=True)


def effective_settings(*, env_root=None):
    """Saved provider selection wins completely; never mix environment secrets."""
    path = settings_path()
    if path.is_symlink():
        raise ValueError("后台模型配置路径不能是符号链接")
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return validate_settings(payload, source="saved")
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("保存的后台模型配置不可用；已阻止回退到其他凭据") from exc
    return _environment_settings(env_root)


def public_view(settings):
    provider = settings["provider"]
    configured = provider != "disabled" and bool(settings.get("api_key"))
    return {
        "provider": provider,
        "base_url": settings["base_url"],
        "model": "Pi Chat Agent" if provider == "pichat" else settings["model"],
        "timeout_seconds": settings["timeout_seconds"],
        "api_key_configured": bool(settings.get("api_key")) and provider != "disabled",
        "configured": configured,
        "source": settings["source"],
        "protocol": (
            "Pi Chat /api/v1/chat" if provider == "pichat"
            else "兼容 Chat Completions" if provider != "disabled" else "未启用"
        ),
    }


def public_settings(*, env_root=None):
    """Keep optional model failures out of health/status and repair interfaces.

    This is only a non-secret projection. Execution continues to read
    effective_settings() directly and must never fall back after an error.
    """
    try:
        return public_view(effective_settings(env_root=env_root))
    except (OSError, ValueError, TypeError):
        unavailable = public_view(validate_settings({"provider": "disabled"}, source="error"))
        return {
            **unavailable,
            "config_error": (
                "后台模型配置无法读取或格式无效，模型调用已停止，未回退到其他凭据。"
                "请填写完整的新配置与新 Key，或保存禁用配置；若保存失败，请检查数据目录权限。"
            ),
        }


def prepare_settings(payload):
    """Make a private draft; blank keys only inherit for the exact same target."""
    draft = validate_settings(payload, source="saved")
    if draft["provider"] != "disabled" and not draft["api_key"]:
        current = effective_settings()
        if (
            current["provider"] == draft["provider"]
            and endpoint_for(current) == endpoint_for(draft)
        ):
            draft["api_key"] = current["api_key"]
    return draft


def save_settings(payload):
    draft = prepare_settings(payload)
    path = settings_path()
    if path.is_symlink():
        raise ValueError("后台模型配置路径不能是符号链接")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".model-settings-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump({key: value for key, value in draft.items() if key != "source"}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return public_view(draft)
