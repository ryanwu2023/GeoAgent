"""Agent/model transports, hot-readable local configuration, no browser secrets."""
import os
from urllib.parse import urlsplit
import httpx
from .store import ROOT

def config():
    keys=('PICHAT_BASE_URL','PICHAT_API_KEY','PICHAT_TIMEOUT','LLM_BASE_URL','LLM_MODEL','LLM_API_KEY','LLM_TIMEOUT')
    values={k:os.getenv(k,'') for k in keys}
    path=ROOT/'.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1)
                if key.strip() in values:values[key.strip()]=value.strip().strip('\"').strip("'")
    # Explicit process configuration wins; app startup does not copy .env model values.
    for key in keys:
        if os.getenv(key):values[key]=os.environ[key]
    values['PICHAT_BASE_URL']=values['PICHAT_BASE_URL'] or 'http://127.0.0.1:9172'
    values['LLM_BASE_URL']=values['LLM_BASE_URL'] or 'https://api.openai.com/v1'
    timeout_value=values['PICHAT_TIMEOUT'] if values['PICHAT_API_KEY'] else values['LLM_TIMEOUT']
    try:values['timeout']=max(5,min(300,float(timeout_value or (90 if values['PICHAT_API_KEY'] else 45))))
    except ValueError:values['timeout']=45
    return values

def endpoint(url):
    url=url.rstrip('/')
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('模型地址需为不含凭据与查询参数的 HTTP(S) 地址')
    return url if url.endswith('/chat/completions') else url+'/chat/completions'

def pichat_endpoint(url):
    url=url.rstrip('/')
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Pi Chat 地址需为不含凭据与查询参数的 HTTP(S) 地址')
    return url if url.endswith('/api/v1/chat') else url+'/api/v1/chat'

def status():
    c=config()
    if c['PICHAT_API_KEY']:
        return {'configured':True,'model':'Pi Chat Agent','mode':'Agent 增强分析','provider':'pichat','protocol':'Pi Chat /api/v1/chat','auto_model':False}
    return {'configured':bool(c['LLM_API_KEY']),'model':c['LLM_MODEL'] or '自动发现','mode':'模型分析' if c['LLM_API_KEY'] else '本地证据检索','provider':'openai-compatible' if c['LLM_API_KEY'] else 'local','protocol':'兼容 Chat Completions','auto_model':not bool(c['LLM_MODEL'])}

async def resolve_model(client,c):
    if c['LLM_MODEL']:return c['LLM_MODEL']
    base=endpoint(c['LLM_BASE_URL']).removesuffix('/chat/completions')
    response=await client.get(base+'/models',headers={'Authorization':'Bearer '+c['LLM_API_KEY']})
    response.raise_for_status()
    names=[x['id'] for x in response.json().get('data',[]) if isinstance(x,dict) and isinstance(x.get('id'),str) and x['id'] and not any(k in x['id'].lower() for k in ('embedding','whisper','tts','dall-e'))]
    if not names:raise ValueError('服务未返回可用模型，请在 .env 填写 LLM_MODEL')
    return names[0]

async def complete(messages):
    c=config()
    if c['PICHAT_API_KEY']:
        prompt='\n\n'.join(f"[{str(item.get('role','user')).upper()}]\n{item.get('content','')}" for item in messages)
        async with httpx.AsyncClient(timeout=c['timeout']) as client:
            response=await client.post(pichat_endpoint(c['PICHAT_BASE_URL']),headers={'Authorization':'Bearer '+c['PICHAT_API_KEY']},json={'message':prompt,'stream':False})
            response.raise_for_status()
            content=response.json().get('text')
            if not isinstance(content,str) or not content.strip():raise ValueError('Pi Chat Agent 未返回文本内容')
            return content,'Pi Chat Agent'
    if not c['LLM_API_KEY']:raise ValueError('尚未配置 Agent 或模型密钥')
    async with httpx.AsyncClient(timeout=c['timeout']) as client:
        model=await resolve_model(client,c)
        response=await client.post(endpoint(c['LLM_BASE_URL']),headers={'Authorization':'Bearer '+c['LLM_API_KEY']},json={'model':model,'messages':messages})
        response.raise_for_status()
        content=response.json()['choices'][0]['message']['content']
        if not isinstance(content,str):raise ValueError('模型未返回文本内容')
        return content,model

def error_message(exc):
    if isinstance(exc,httpx.TimeoutException):return '模型响应超时，请检查服务或增加 LLM_TIMEOUT'
    if isinstance(exc,httpx.HTTPStatusError):
        code=exc.response.status_code
        return {401:'Agent 或模型密钥无效',403:'Agent 或模型权限不足',404:'Agent 或模型地址不存在',429:'Agent 并发、额度或调用频率受限'}.get(code,f'Agent 或模型服务返回 HTTP {code}')
    if isinstance(exc,httpx.RequestError):return '无法连接模型服务，请检查 URL 和网络'
    return '模型配置、响应结构或引用校验未通过；自动发现不可用时请填写 LLM_MODEL'
