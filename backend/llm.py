"""Agent/model transports, hot-readable local configuration, no browser secrets."""
import hashlib
import httpx
from .store import ROOT
from . import model_settings
from .integrations import pichat_tls_context

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
    return {
        **view,
        "model": view["model"] or ("自动发现" if view["configured"] else "未接入模型"),
        "mode": (
            "Agent 增强分析" if view["provider"] == "pichat" and view["configured"]
            else "模型分析" if view["configured"] else "本地证据检索"
        ),
        "auto_model": bool(view["configured"] and view["provider"] != "pichat" and not view["model"]),
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

async def resolve_model(client,c):
    if c["model"]:return c["model"]
    response=await client.get(c["base_url"]+'/models',headers={'Authorization':'Bearer '+c["api_key"]})
    response.raise_for_status()
    names=[x['id'] for x in response.json().get('data',[]) if isinstance(x,dict) and isinstance(x.get('id'),str) and x['id'] and not any(k in x['id'].lower() for k in ('embedding','whisper','tts','dall-e'))]
    if not names:raise ValueError('服务未返回可用模型，请在 .env 填写 LLM_MODEL')
    return names[0]

async def complete(messages, *, settings_override=None):
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
        prompt='\n\n'.join(f"[{str(item.get('role','user')).upper()}]\n{item.get('content','')}" for item in messages)
        async with httpx.AsyncClient(timeout=c["timeout_seconds"], verify=verify, follow_redirects=False) as client:
            response=await client.post(model_settings.endpoint_for(c),headers={'Authorization':'Bearer '+c["api_key"]},json={'message':prompt,'stream':False})
            response.raise_for_status()
            content=response.json().get('text')
            if not isinstance(content,str) or not content.strip():raise ValueError('Pi Chat Agent 未返回文本内容')
            return content,'Pi Chat Agent'
    async with httpx.AsyncClient(timeout=c["timeout_seconds"], verify=verify, follow_redirects=False) as client:
        model=await resolve_model(client,c)
        payload={'model':model,'messages':messages,'stream':False}
        if c["provider"] == "deepseek":
            payload["thinking"]={"type":"disabled"}
        response=await client.post(model_settings.endpoint_for(c),headers={'Authorization':'Bearer '+c["api_key"]},json=payload)
        response.raise_for_status()
        choice=response.json()['choices'][0]
        if "finish_reason" in choice and choice["finish_reason"] != "stop":
            raise ValueError('模型返回未完整完成的结果')
        content=choice['message']['content']
        if not isinstance(content,str) or not content.strip():raise ValueError('模型未返回文本内容')
        return content,model

def error_message(exc):
    if isinstance(exc,httpx.TimeoutException):return '模型响应超时，请检查服务或增加后台模型超时设置'
    if isinstance(exc,httpx.HTTPStatusError):
        code=exc.response.status_code
        return {401:'Agent 或模型密钥无效',403:'Agent 或模型权限不足',404:'Agent 或模型地址不存在',429:'Agent 并发、额度或调用频率受限'}.get(code,f'Agent 或模型服务返回 HTTP {code}')
    if isinstance(exc,httpx.RequestError):return '无法连接模型服务，请检查 URL 和网络'
    return '模型配置、响应结构或引用校验未通过；请在连接配置页核对服务地址与模型名称'
