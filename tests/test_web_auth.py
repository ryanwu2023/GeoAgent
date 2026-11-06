"""Real PostgreSQL browser sessions, independently enforced from MCP auth."""
import hashlib
import json
from pathlib import Path

import httpx

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from passlib.hash import sha512_crypt

from backend.database import connect
from backend.web_auth import COOKIE, WebAuthMiddleware, router
from backend.geo_mcp.auth import AuthConfig, BearerAuthMiddleware

HEADERS = {'X-GeoAgent-Login': '1', 'Origin': 'https://testserver'}

@pytest.fixture
def browser(monkeypatch, tmp_path):
    path=tmp_path/'auth'
    path.write_text('researcher:'+sha512_crypt.using(rounds=5000).hash('correct-test-password')+'\n')
    monkeypatch.setenv('GEO_WEB_AUTH_ENABLED','true')
    monkeypatch.setenv('GEO_WEB_AUTH_FILE',str(path))
    monkeypatch.setenv('GEOAGENT_ALLOWED_ORIGINS','https://testserver')
    app=FastAPI()
    app.add_middleware(BearerAuthMiddleware,config=AuthConfig(token_hash=None,principal=None))
    app.add_middleware(WebAuthMiddleware)
    app.include_router(router)
    @app.get('/api/private')
    def private(): return {'private':True}
    @app.post('/api/private')
    def write(): return {'written':True}
    @app.get('/')
    def home(): return {'home':True}
    with TestClient(app,base_url='https://testserver') as client:
        yield client,path

@pytest.fixture
def pichat(monkeypatch):
    """Fake PiChat embed identity endpoint; set state['response'] to change the reply."""
    state={'calls':[],'response':None}
    def handler(request):
        state['calls'].append(request)
        if isinstance(state['response'],Exception):
            raise state['response']
        if state['response'] is not None:
            return state['response']
        body=json.loads(request.content)
        return httpx.Response(200,json={'ok':True,'data':{'token':'panel-token','userId':'pc-'+body['productUserId'][-8:],'agentId':'geo'}})
    monkeypatch.setenv('PICHAT_EMBED_BASE_URL','https://pichat.test')
    monkeypatch.setenv('PICHAT_EMBED_APP_KEY','pk-test-key')
    monkeypatch.setenv('PICHAT_EMBED_PHONE','+8613000000000')
    real=httpx.Client
    monkeypatch.setattr('backend.web_auth.httpx.Client',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    return state

def login(client, password='correct-test-password'):
    return client.post('/api/auth/login',json={'username':'researcher','password':password},headers=HEADERS)

def test_browser_login_logout_rotation_and_api_gate(browser):
    client,_=browser
    response=client.get('/',follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/login'
    assert 'www-authenticate' not in response.headers
    assert client.get('/api/private').status_code==401
    assert client.get('/api/auth/session').json()['authenticated'] is False
    assert login(client,'bad').status_code==401
    response=login(client)
    assert response.status_code==200
    cookie=response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
    old=client.cookies.get(COOKIE)
    assert client.get('/api/private').status_code==200
    assert client.get('/api/auth/session').json()['username']=='researcher'
    with connect() as conn:
        row=conn.execute('SELECT token_hash FROM web_sessions').fetchone()
        assert row[0]==hashlib.sha256(old.encode()).hexdigest()
    assert login(client).status_code==200
    new=client.cookies.get(COOKIE)
    assert old!=new
    client.cookies.clear();client.cookies.set(COOKIE,old)
    assert client.get('/api/private').status_code==401
    client.cookies.clear();client.cookies.set(COOKIE,new)
    assert client.post('/api/auth/logout',json={},headers=HEADERS).status_code==200
    client.cookies.set(COOKIE,new)
    assert client.get('/api/private').status_code==401

def test_expired_and_changed_credentials_revoke_session(browser):
    client,path=browser
    assert login(client).status_code==200
    with connect() as conn:conn.execute("UPDATE web_sessions SET expires_at=CURRENT_TIMESTAMP - INTERVAL '1 second'")
    assert client.get('/api/private').status_code==401
    assert login(client).status_code==200
    path.write_text('researcher:'+sha512_crypt.hash('changed-password')+'\n')
    assert client.get('/api/private').status_code==401

def test_mcp_never_accepts_web_session_or_basic(browser):
    client,_=browser
    assert login(client).status_code==200
    for path in ('/mcp','/mcp/'):
        response=client.post(path,json={})
        assert response.status_code==401
        assert response.headers['www-authenticate']=='Bearer'
    client.cookies.clear()
    assert client.get('/api/private',headers={'Authorization':'Basic cmVzZWFyY2hlcjpjb3JyZWN0LXRlc3QtcGFzc3dvcmQ=','X-GeoAgent-Gateway-User':'researcher'}).status_code==401

def test_cross_site_login_and_mutations_rejected(browser):
    client,_=browser
    data={'username':'researcher','password':'correct-test-password'}
    assert client.post('/api/auth/login',json=data).status_code==403
    assert client.post('/api/auth/login',json=data,headers={**HEADERS,'Origin':'https://evil.example'}).status_code==403
    assert login(client).status_code==200
    assert client.post('/api/private',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/private',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    assert client.post('/api/private',headers=HEADERS).status_code==200

def test_auth_file_missing_fails_closed(browser):
    client,path=browser
    path.unlink()
    assert client.get('/api/auth/session').status_code==503
    assert login(client).status_code==503
    assert client.get('/api/private').status_code==401

def test_login_attempts_are_limited_in_database(browser):
    client,_=browser
    for _ in range(10):assert login(client,'wrong').status_code==401
    assert login(client).status_code==429

@pytest.mark.parametrize('path',['/api/private/','/api/private%2fextra','/docs','/openapi.json'])
def test_alternate_paths_never_bypass_auth(browser,path):
    client,_=browser
    assert client.get(path,follow_redirects=False).status_code in (303,401)

def login_as(client, username, password):
    return client.post('/api/auth/login',json={'username':username,'password':password},headers=HEADERS)

def test_admin_creates_user_with_default_password_and_user_cannot_manage(browser,pichat):
    client,_=browser
    assert client.get('/api/auth/users').status_code==401
    assert login(client).json()['is_admin'] is True
    assert client.get('/api/auth/session').json()['is_admin'] is True
    response=client.post('/api/auth/users',json={'username':'analyst01'},headers=HEADERS)
    assert response.status_code==201 and response.json()['default_password'] is True
    assert client.post('/api/auth/users',json={'username':'analyst01'},headers=HEADERS).status_code==409
    assert client.post('/api/auth/users',json={'username':'researcher'},headers=HEADERS).status_code==409
    assert client.post('/api/auth/users',json={'username':'bad name'},headers=HEADERS).status_code==422
    assert client.post('/api/auth/users',json={'username':'x2'}).status_code==403  # login header required
    listed=client.get('/api/auth/users').json()
    assert listed['admins']==['researcher'] and [u['username'] for u in listed['users']]==['analyst01']
    assert listed['users'][0]['created_by']=='researcher'
    with connect() as conn:
        stored=conn.execute("SELECT password_hash FROM web_users WHERE username='analyst01'").fetchone()[0]
    assert stored.startswith('$6$') and 'analyst01' not in stored
    client.cookies.clear()
    assert login_as(client,'analyst01','wrong').status_code==401
    assert login_as(client,'analyst01','analyst01').json()['is_admin'] is False
    assert client.get('/api/private').status_code==200
    assert client.get('/api/auth/session').json()=={'enabled':True,'authenticated':True,'username':'analyst01','is_admin':False}
    assert client.get('/api/auth/users').status_code==403
    assert client.post('/api/auth/users',json={'username':'other'},headers=HEADERS).status_code==403
    assert client.put('/api/auth/users/analyst01/password',json={'password':'x'},headers=HEADERS).status_code==403
    assert client.delete('/api/auth/users/analyst01',headers=HEADERS).status_code==403

def test_admin_resets_password_without_old_one_and_revokes_sessions(browser,pichat):
    client,_=browser
    admin=TestClient(client.app,base_url='https://testserver')
    assert login(admin).status_code==200
    assert admin.post('/api/auth/users',json={'username':'analyst02','password':'initial-pass'},headers=HEADERS).status_code==201
    assert login_as(client,'analyst02','initial-pass').status_code==200
    with connect() as conn:
        created=conn.execute("SELECT created_at,data_timestamp FROM web_users WHERE username='analyst02'").fetchone()
    response=admin.put('/api/auth/users/analyst02/password',json={'password':'new-pass-2026'},headers=HEADERS)
    assert response.status_code==200 and response.json()['default_password'] is False
    assert client.get('/api/private').status_code==401
    assert login_as(client,'analyst02','initial-pass').status_code==401
    assert login_as(client,'analyst02','new-pass-2026').status_code==200
    assert admin.put('/api/auth/users/analyst02/password',json={},headers=HEADERS).json()['default_password'] is True
    assert login_as(client,'analyst02','analyst02').status_code==200
    with connect() as conn:
        row=conn.execute("SELECT created_by,updated_by,updated_at,created_at,data_timestamp FROM web_users WHERE username='analyst02'").fetchone()
    # Docker Desktop clocks can step backwards, so compare against the stored creation values.
    assert row['created_by']=='researcher' and row['updated_by']=='researcher' and row['updated_at'] is not None
    assert (row['created_at'],row['data_timestamp'])==(created['created_at'],created['data_timestamp'])
    assert admin.put('/api/auth/users/researcher/password',json={'password':'x'},headers=HEADERS).status_code==409
    assert admin.put('/api/auth/users/nobody/password',json={},headers=HEADERS).status_code==404

def test_deleted_user_is_logged_out_and_name_can_be_reused(browser,pichat):
    client,_=browser
    admin=TestClient(client.app,base_url='https://testserver')
    assert login(admin).status_code==200
    assert admin.post('/api/auth/users',json={'username':'analyst03'},headers=HEADERS).status_code==201
    assert login_as(client,'analyst03','analyst03').status_code==200
    assert admin.delete('/api/auth/users/analyst03',headers=HEADERS).status_code==200
    assert client.get('/api/private').status_code==401
    assert login_as(client,'analyst03','analyst03').status_code==401
    assert admin.get('/api/auth/users').json()['users']==[]
    assert admin.delete('/api/auth/users/analyst03',headers=HEADERS).status_code==404
    assert admin.delete('/api/auth/users/researcher',headers=HEADERS).status_code==409
    assert admin.post('/api/auth/users',json={'username':'analyst03'},headers=HEADERS).status_code==201
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM web_users WHERE username='analyst03'").fetchone()[0]==2

def test_web_users_schema_follows_common_fields_and_comments(browser):
    with connect() as conn:
        columns={r['column_name']:r for r in conn.execute("SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='web_users'").fetchall()}
        comments=conn.execute("SELECT count(*) FILTER (WHERE col_description('web_users'::regclass,attnum) IS NULL) AS missing, obj_description('web_users'::regclass) AS tbl FROM pg_attribute WHERE attrelid='web_users'::regclass AND attnum>0 AND NOT attisdropped").fetchone()
    for name in ('created_at','updated_at','data_timestamp'):
        assert columns[name]['data_type']=='timestamp with time zone'
    assert {n:columns[n]['is_nullable'] for n in ('created_by','created_at','updated_by','updated_at','data_timestamp','is_deleted')}=={'created_by':'YES','created_at':'NO','updated_by':'YES','updated_at':'YES','data_timestamp':'NO','is_deleted':'NO'}
    assert comments['missing']==0 and comments['tbl']

def test_creating_user_materializes_pichat_shadow_user(browser,pichat):
    client,_=browser
    assert login(client).status_code==200
    response=client.post('/api/auth/users',json={'username':'analyst04'},headers=HEADERS)
    assert response.status_code==201 and response.json()['phone_generated'] is True
    call=pichat['calls'][0]
    assert str(call.url)=='https://pichat.test/api/v1/embed/identity'
    assert call.headers['Authorization']=='Bearer pk-test-key'
    sent=json.loads(call.content)
    assert sent['productUserId'].startswith('geo-') and len(sent['productUserId'])==36
    assert sent['phone'].startswith('+999') and len(sent['phone'])==15
    assert sent['displayName']=='analyst04'
    with connect() as conn:
        row=conn.execute("SELECT product_user_id,phone,pichat_user_id FROM web_users WHERE username='analyst04'").fetchone()
    assert (row['product_user_id'],row['phone'])==(sent['productUserId'],sent['phone'])
    assert row['pichat_user_id']==response.json()['pichat_user_id']=='pc-'+sent['productUserId'][-8:]
    listed=client.get('/api/auth/users').json()['users'][0]
    assert listed['phone']==sent['phone'] and listed['pichat_user_id']==row['pichat_user_id']

def test_explicit_phone_is_normalized_and_unique(browser,pichat):
    client,_=browser
    assert login(client).status_code==200
    response=client.post('/api/auth/users',json={'username':'analyst05','phone':'138 1234 5678'},headers=HEADERS)
    assert response.status_code==201 and response.json()['phone_generated'] is False
    assert json.loads(pichat['calls'][0].content)['phone']=='+8613812345678'
    assert client.post('/api/auth/users',json={'username':'analyst06','phone':'+8613812345678'},headers=HEADERS).status_code==409
    assert client.post('/api/auth/users',json={'username':'analyst07','phone':'12345'},headers=HEADERS).status_code==422
    assert len(pichat['calls'])==1
    # A recreated name is a new PiChat user, never the deleted one's history.
    assert client.delete('/api/auth/users/analyst05',headers=HEADERS).status_code==200
    assert client.post('/api/auth/users',json={'username':'analyst05'},headers=HEADERS).status_code==201
    first,second=(json.loads(c.content)['productUserId'] for c in pichat['calls'])
    assert first!=second

@pytest.mark.parametrize('reply',[
    httpx.Response(200,json={'ok':False,'error':{'code':'embed_identity_mint_failed','message':'EMBED_IDENTITY_ALIAS_CONFLICT'}}),
    httpx.Response(500,text='boom'),
    httpx.Response(401,json={'ok':False,'error':{'message':'private upstream detail'}}),
])
def test_pichat_failure_creates_nothing(browser,pichat,reply):
    client,_=browser
    assert login(client).status_code==200
    pichat['response']=reply
    response=client.post('/api/auth/users',json={'username':'analyst08'},headers=HEADERS)
    assert response.status_code==502 and '用户未创建' in response.json()['detail']
    assert 'private upstream detail' not in response.text and 'pk-test-key' not in response.text
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM web_users").fetchone()[0]==0
    client.cookies.clear()
    assert login_as(client,'analyst08','analyst08').status_code==401

def test_pichat_unreachable_or_unconfigured_creates_nothing(browser,pichat,monkeypatch):
    client,_=browser
    assert login(client).status_code==200
    pichat['response']=httpx.ConnectError('refused')
    assert client.post('/api/auth/users',json={'username':'analyst09'},headers=HEADERS).status_code==502
    monkeypatch.setenv('PICHAT_EMBED_APP_KEY','')
    assert client.post('/api/auth/users',json={'username':'analyst09'},headers=HEADERS).status_code==503
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM web_users").fetchone()[0]==0

def test_panel_identity_uses_each_users_own_pichat_subject(browser,pichat,monkeypatch):
    from backend.app import pichat_embed_identity
    client,_=browser
    client.app.add_api_route('/api/pichat/embed-identity',pichat_embed_identity)
    minted=[]
    def handler(request):
        minted.append(json.loads(request.content))
        return httpx.Response(200,json={'ok':True,'data':{'token':'panel-token','agentId':'geo'}})
    real=httpx.AsyncClient
    monkeypatch.setattr('backend.app.httpx.AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    admin=TestClient(client.app,base_url='https://testserver')
    assert login(admin).status_code==200
    assert admin.post('/api/auth/users',json={'username':'analyst10','phone':'+85251234567'},headers=HEADERS).status_code==201
    created=json.loads(pichat['calls'][0].content)
    assert admin.get('/api/pichat/embed-identity').status_code==200
    assert login_as(client,'analyst10','analyst10').status_code==200
    assert client.get('/api/pichat/embed-identity').status_code==200
    admin_subject,user_subject=minted
    assert (admin_subject['productUserId'],admin_subject['phone'])==('geoagent-local','+8613000000000')
    assert (user_subject['productUserId'],user_subject['phone'],user_subject['displayName'])==(created['productUserId'],'+85251234567','analyst10')
