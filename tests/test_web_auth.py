"""Real PostgreSQL browser sessions, independently enforced from MCP auth."""
import hashlib
from pathlib import Path

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
