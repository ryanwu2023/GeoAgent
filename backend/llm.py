"""Agent/model transports, hot-readable local configuration, no browser secrets."""
import hashlib
import json
import os
import httpx
from .store import ROOT
from . import model_settings
from .integrations import pichat_tls_context
from .qa_model_client import ModelReply, ToolProtocolUnsupported

def config():
    current = model_settings.effective_settings(env_root=ROOT)
    # Legacy internal readers still use these names. Populate only the selected
    # provider, never append an environment key to a saved selection.
    pichat = current["provider"] == "pichat"
    return {
        **current, "timeout": current["timeout_seconds"],
        "PICHAT_BASE_URL": current["base_url"] if pichat else "",
        "PICHAT_API_KEY": current["api_key"] if pichat else "",
        "LLM_BASE_URL": current["base_url"] if not pichat else "",
        "LLM_API_KEY": current["api_key"] if not pichat else "",
        "LLM_MODEL": current["model"] if not pichat else "",
    }

def endpoint(url):
    return model_settings.normalize_base_url(url, "openai-compatible", allow_legacy_http=True) + "/chat/completions"

def pichat_endpoint(url):
    return model_settings.normalize_base_url(url, "pichat", allow_legacy_http=True) + "/api/v1/chat"

def status():
    view = model_settings.public_settings(env_root=ROOT)
    fast_model = role_model("fast", fallback=view["model"])
    reasoning_model = role_model("reasoning", fallback=view["model"])
    return {
        **view,
        "model": view["model"] or ("自动发现" if view["configured"] else "未接入模型"),
        "mode": (
            "Agent 增强分析" if view["provider"] == "pichat" and view["configured"]
            else "模型分析" if view["configured"] else "本地证据检索"
        ),
        "auto_model": bool(view["configured"] and view["provider"] != "pichat" and not view["model"]),
        "qa_rounds": 1 if view["provider"] == "pichat" else qa_rounds(),
        "agent_mode": "pichat" if view["provider"] == "pichat" else agent_mode(),
        "fast_model": "Pi Chat Agent" if view["provider"] == "pichat" else fast_model,
        "reasoning_model": "Pi Chat Agent" if view["provider"] == "pichat" else reasoning_model,
        "reasoning_model_fallback": bool(view["provider"] != "pichat" and reasoning_model and reasoning_model == view["model"]),
    }

def cache_identity():
    """Return a non-secret identity used to invalidate persisted model output."""
    c = model_settings.public_settings(env_root=ROOT)
    provider = c["provider"]
    model = "Pi Chat Agent" if provider == "pichat" else c["model"] or "自动发现"
    target = model_settings.endpoint_for(c) if provider != "disabled" else ""
    return {
        'provider': provider,
        'model': model,
        'endpoint_hash': hashlib.sha256(target.encode('utf-8')).hexdigest()[:16],
    }

def qa_rounds(default=3):
    """Return the bounded loop count for direct-model Q&A.

    This is deliberately independent from saved provider credentials: the
    loop count is operational policy, not a secret.  Read the project .env so
    start.bat users get the same behaviour as process-level deployments.
    """
    raw = os.getenv("LLM_QA_ROUNDS", "").strip()
    if not raw:
        path = ROOT / ".env"
        if path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                key, value = line.split("=", 1)
                if key.strip() == "LLM_QA_ROUNDS":
                    raw = value.strip().strip('"').strip("'")
                    break
    try:
        value = int(raw or default)
    except ValueError:
        value = default
    return max(1, min(5, value))


def _project_env(name, default=""):
    value = os.getenv(name, "").strip()
    if value:
        return value
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, raw = line.split("=", 1)
            if key.strip() == name:
                parsed = raw.strip().strip('"').strip("'")
                return parsed or default
    return default


def agent_mode():
    """Select the direct-model orchestration policy without exposing secrets."""
    value = _project_env("LLM_AGENT_MODE", "tools").lower()
    return "tools" if value in {"1", "true", "yes", "on", "tools", "agent"} else "legacy"


def role_model(role, *, fallback=""):
    if role == "fast":
        return _project_env("LLM_FAST_MODEL", fallback)
    if role == "reasoning":
        return _project_env("LLM_REASONING_MODEL", fallback)
    return fallback

async def resolve_model(client,c):
    if c["model"]:return c["model"]
    response=await client.get(c["base_url"]+'/models',headers={'Authorization':'Bearer '+c["api_key"]})
    response.raise_for_status()
    names=[x['id'] for x in response.json().get('data',[]) if isinstance(x,dict) and isinstance(x.get('id'),str) and x['id'] and not any(k in x['id'].lower() for k in ('embedding','whisper','tts','dall-e'))]
    if not names:raise ValueError('服务未返回可用模型，请在 .env 填写 LLM_MODEL')
    return names[0]

def _usage(response, messages, text, tool_calls):
    raw = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    input_tokens = raw.get("prompt_tokens", raw.get("input_tokens"))
    output_tokens = raw.get("completion_tokens", raw.get("output_tokens"))
    estimated = input_tokens is None or output_tokens is None
    if input_tokens is None:
        input_tokens = max(1, len(json.dumps(messages, ensure_ascii=False)) // 4)
    if output_tokens is None:
        output_tokens = max(1, len(text) // 4 + len(json.dumps(tool_calls, ensure_ascii=False)) // 4)
    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "estimated": estimated,
    }


def _tools_rejected(response):
    if response.status_code not in {400, 404, 422}:
        return False
    body = response.text.lower()[:4000]
    return any(field in body for field in ("tools", "tool_choice", "function call")) and any(
        marker in body for marker in ("unsupported", "not support", "unknown", "invalid", "不支持")
    )


async def complete_response(
    messages, *, settings_override=None, model_role="default", tools=None,
    max_output_tokens=None, temperature=None, timeout_seconds=None, reasoning=None,
):
    """调用模型并保留原生工具、用量与结束原因；不会破坏旧文本接口。"""
    if settings_override is None:
        c=config()
    else:
        # An explicit draft is self-contained. Never consult persisted or
        # environment settings while testing a different target.
        c=model_settings.validate_settings(settings_override, source="saved")
    if c["provider"] == "disabled":
        raise ValueError("后台模型已停用")
    if not c["api_key"]:
        raise ValueError("尚未配置后台模型 API Key")
    verify = pichat_tls_context() if c["provider"] == "pichat" else True
    if c["provider"] == "pichat":
        if tools:
            raise ToolProtocolUnsupported("PiChat嵌入接口不提供本地原生工具协议")
        prompt='\n\n'.join(f"[{str(item.get('role','user')).upper()}]\n{item.get('content','')}" for item in messages)
        request_timeout = min(float(c["timeout_seconds"]), float(timeout_seconds or c["timeout_seconds"]))
        async with httpx.AsyncClient(timeout=request_timeout, verify=verify, follow_redirects=False) as client:
            response=await client.post(model_settings.endpoint_for(c),headers={'Authorization':'Bearer '+c["api_key"]},json={'message':prompt,'stream':False})
            response.raise_for_status()
            content=response.json().get('text')
            if not isinstance(content,str) or not content.strip():raise ValueError('Pi Chat Agent 未返回文本内容')
            return ModelReply(
                text=content, tool_calls=[], usage=_usage({}, messages, content, []),
                model='Pi Chat Agent', finish_reason='stop', request_id=str(response.headers.get("x-request-id") or ""),
            )
    request_timeout = min(float(c["timeout_seconds"]), float(timeout_seconds or c["timeout_seconds"]))
    async with httpx.AsyncClient(timeout=request_timeout, verify=verify, follow_redirects=False) as client:
        model=await resolve_model(client,c)
        selected = role_model(model_role, fallback=model) if model_role in {"fast", "reasoning"} else model
        model = selected or model
        payload={'model':model,'messages':messages,'stream':False}
        if tools:
            if not c.get("supports_tools", True):
                raise ToolProtocolUnsupported("当前模型配置明确不支持原生工具协议")
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if max_output_tokens is not None:
            payload["max_tokens"] = int(max_output_tokens)
        selected_temperature = c.get("temperature") if temperature is None else temperature
        if selected_temperature is not None:
            payload["temperature"] = float(selected_temperature)
        reasoning_mode = c.get("reasoning_mode", "auto")
        if reasoning is not None:
            reasoning_mode = "enabled" if reasoning else "disabled"
        if reasoning_mode in {"enabled", "disabled"}:
            payload["thinking"]={"type":reasoning_mode}
            if c["provider"] == "openai-compatible":
                # 自建 vLLM 不认 DeepSeek 官方的 thinking 字段，靠聊天模板参数开关思考；
                # 不同模板分别用 thinking / enable_thinking，未用到的键会被忽略。
                enabled = reasoning_mode == "enabled"
                payload["chat_template_kwargs"] = {"thinking": enabled, "enable_thinking": enabled}
        response=await client.post(model_settings.endpoint_for(c),headers={'Authorization':'Bearer '+c["api_key"]},json=payload)
        if tools and _tools_rejected(response):
            raise ToolProtocolUnsupported("模型网关明确拒绝原生工具字段")
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError('模型响应缺少choices')
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), str) else ""
        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        tool_calls = [dict(item) for item in tool_calls if isinstance(item, dict)]
        if not content.strip() and not tool_calls:
            raise ValueError('模型未返回文本内容或工具调用')
        finish_reason = "stop" if "finish_reason" not in choice else str(choice.get("finish_reason") or "")
        return ModelReply(
            text=content, tool_calls=tool_calls, usage=_usage(data, messages, content, tool_calls),
            model=str(data.get("model") or model),
            finish_reason=finish_reason,
            request_id=str(data.get("id") or response.headers.get("x-request-id") or ""),
        )


async def complete(
    messages, *, settings_override=None, model_role="default",
    max_output_tokens=None, temperature=None, timeout_seconds=None, reasoning=None,
):
    """兼容文本调用接口；翻译、简报及PiChat调用方继续接收二元组。"""
    reply = await complete_response(
        messages, settings_override=settings_override, model_role=model_role,
        max_output_tokens=max_output_tokens, temperature=temperature,
        timeout_seconds=timeout_seconds, reasoning=reasoning,
    )
    if reply.finish_reason != "stop":
        raise ValueError('模型返回未完整完成的结果')
    if not reply.text.strip():
        raise ValueError('模型未返回文本内容')
    return reply.text, reply.model

def error_message(exc):
    if isinstance(exc,httpx.TimeoutException):return '模型响应超时，请检查服务或增加后台模型超时设置'
    if isinstance(exc,httpx.HTTPStatusError):
        code=exc.response.status_code
        return {401:'Agent 或模型密钥无效',403:'Agent 或模型权限不足',404:'Agent 或模型地址不存在',429:'Agent 并发、额度或调用频率受限'}.get(code,f'Agent 或模型服务返回 HTTP {code}')
    if isinstance(exc,httpx.RequestError):return '无法连接模型服务，请检查 URL 和网络'
    return '模型配置、响应结构或引用校验未通过；请在连接配置页核对服务地址与模型名称'
