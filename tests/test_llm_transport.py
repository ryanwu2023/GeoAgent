import asyncio
import json
import httpx
import pytest
from backend import llm
from backend.translation_quality import language,quality,normalize_terms

def test_endpoint_full_and_base():
    assert llm.endpoint('https://provider.example/v1/')=='https://provider.example/v1/chat/completions'
    assert llm.endpoint('https://provider.example/v1/chat/completions')=='https://provider.example/v1/chat/completions'
    with pytest.raises(ValueError):llm.endpoint('https://key@provider.example/v1')

def test_hot_config_and_discovery(tmp_path,monkeypatch):
    monkeypatch.setattr(llm,'ROOT',tmp_path)
    file=tmp_path/'.env';file.write_text('LLM_BASE_URL=https://provider.example/v1\nLLM_API_KEY=test-only\n',encoding='utf-8')
    assert llm.status()['configured'] and llm.status()['auto_model']
    calls=[]
    def handler(request):
        calls.append(request)
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':'embedding-test'},{'id':'chat-test'}]})
        assert json.loads(request.content)['model']=='chat-test'
        return httpx.Response(200,json={'choices':[{'message':{'content':'连接成功'}}]})
    real=httpx.AsyncClient
    monkeypatch.setattr(llm.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    text,model=asyncio.run(llm.complete([{'role':'user','content':'测试'}]))
    assert text=='连接成功' and model=='chat-test' and len(calls)==2
    file.write_text('LLM_MODEL=explicit\nLLM_API_KEY=changed\n',encoding='utf-8')
    assert llm.config()['LLM_MODEL']=='explicit'
    assert 'changed' not in str(llm.status())

def test_safe_errors_hide_provider_body():
    response=httpx.Response(401,request=httpx.Request('POST','https://example.org'),text='secret')
    assert llm.error_message(httpx.HTTPStatusError('secret',request=response.request,response=response))=='模型密钥无效'

def test_language_and_translation_guards():
    assert language('الحرب في المنطقة')=='arabic_unknown'
    assert language('گفتگو در تهران')=='fa'
    assert language('Россия Украина')=='cyrillic'
    assert quality('The shipment has 12 units','交付十件')['issues']
    assert '外文' in ''.join(quality('The story','报道 the latest news today')['issues'])
    assert normalize_terms('Zelenskyy and Houthis')=='泽连斯基 and 胡塞武装'
