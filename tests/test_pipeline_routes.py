import threading

import pytest
from fastapi.testclient import TestClient
from passlib.hash import sha512_crypt

from backend import pipeline_routes
from backend.app import app
from backend.collector_jobs import CollectorBusyError
from backend.pipeline_service import Actor, PipelineError, create_pipeline
from backend.store import db

ROW = {"name": "路透社", "description": "d", "topic": "usiran", "interval_sec": 3600}


def cfg(term="iran"):
    return {"sources": [{"id": "s0", "type": "rss", "name": "源0", "feed_url": "https://example.com/0.xml",
                         "allowed_hosts": ["example.com"], "language": "en", "tier": "T1",
                         "content_policy": "public_fulltext"}],
            "filters": {"include_any": [term]}}


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "true")
    monkeypatch.setattr(pipeline_routes, "default_resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(pipeline_routes, "_preflight", lambda: None)


@pytest.fixture
def client():
    return TestClient(app, base_url="https://testserver")


def body(**kw):
    return {"row": {**ROW, **kw}, "config": cfg()}


def create(client, **kw):
    r = client.post("/api/pipelines", json=body(**kw))
    assert r.status_code == 201, r.text
    return r.json()


def test_crud_roundtrip(client):
    p = create(client)
    assert p["current_version"] == 1 and p["config"]["sources"][0]["id"] == "s0"
    assert r_ok(client.get("/api/pipelines"))["items"][0]["id"] == p["id"]
    assert "server_time" in client.get("/api/pipelines").json()
    assert client.get(f"/api/pipelines/{p['id']}").json()["name"] == "路透社"
    r = client.patch(f"/api/pipelines/{p['id']}", headers={"If-Match": "1"},
                     json={"row": {"description": "新"}, "config": cfg("hormuz"), "change_note": "改"})
    assert r.status_code == 200 and r.json()["description"] == "新" and r.json()["current_version"] == 2
    assert client.post(f"/api/pipelines/{p['id']}/enable").json()["enabled"] is True
    assert client.post(f"/api/pipelines/{p['id']}/disable").json()["enabled"] is False
    r = client.post(f"/api/pipelines/{p['id']}/rollback", json={"to_version": 1, "expected_version": 2})
    assert r.status_code == 200 and r.json()["current_version"] == 3
    assert len(client.get(f"/api/pipelines/{p['id']}/versions").json()) == 3
    assert client.get(f"/api/pipelines/{p['id']}/runs").json() == []
    assert client.delete(f"/api/pipelines/{p['id']}").status_code == 204
    assert client.get(f"/api/pipelines/{p['id']}").status_code == 404
    assert client.get(f"/api/pipelines/{p['id']}/versions").status_code == 404
    assert client.get(f"/api/pipelines/{p['id']}/runs").status_code == 404


def r_ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_responses_are_no_store(client):
    assert client.get("/api/pipelines").headers["cache-control"] == "no-store"


def test_patch_without_if_match_is_428(client):
    p = create(client)
    r = client.patch(f"/api/pipelines/{p['id']}", json={"row": {}, "config": None})
    assert r.status_code == 428 and r.json()["detail"] == "缺少 If-Match 版本号"
    r = client.patch(f"/api/pipelines/{p['id']}", headers={"If-Match": "abc"}, json={"row": {}, "config": None})
    assert r.status_code == 422


def test_patch_stale_version_is_409_with_current_version(client):
    p = create(client)
    r = client.patch(f"/api/pipelines/{p['id']}", headers={"If-Match": "9"}, json={"row": {"description": "x"}})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "VERSION_CONFLICT" and d["current_version"] == 1 and d["message"]


def test_invalid_config_is_422_with_field(client):
    r = client.post("/api/pipelines", json={"row": ROW, "config": {"sources": [], "filters": {"include_any": ["a"]}}})
    assert r.status_code == 422
    assert r.json()["detail"]["field"] and r.json()["detail"]["message"]
    r = client.post("/api/pipelines", json=body(topic="x"))
    assert r.status_code == 422 and r.json()["detail"]["field"] == "row.topic"


def test_missing_pipeline_is_404(client):
    assert client.get("/api/pipelines/p-nope").status_code == 404
    assert client.get("/api/pipelines/p-nope").json()["detail"]["code"] == "NOT_FOUND"


def test_builtin_readonly_is_422(client):
    from backend.pipeline_scheduler import sync_builtin_pipelines
    sync_builtin_pipelines([{"id": "bi1", "name_zh": "内置", "topics": ["usiran"]}])
    items = client.get("/api/pipelines?kind=builtin").json()["items"]
    assert items
    r = client.delete(f"/api/pipelines/{items[0]['id']}")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "BUILTIN_READONLY"


def test_remote_mode_rejects_writes_but_allows_reads(client, monkeypatch):
    p = create(client)
    monkeypatch.setenv("GEO_COLLECTION_MODE", "remote")
    msg = "远端采集模式下此操作需在采集端（Sealos）处理"
    assert client.get("/api/pipelines").status_code == 200
    assert client.get(f"/api/pipelines/{p['id']}").status_code == 200
    for r in (client.post("/api/pipelines", json=body(name="b")),
              client.patch(f"/api/pipelines/{p['id']}", headers={"If-Match": "1"}, json={"row": {}}),
              client.delete(f"/api/pipelines/{p['id']}"),
              client.post(f"/api/pipelines/{p['id']}/enable"),
              client.post(f"/api/pipelines/{p['id']}/run"),
              client.post("/api/pipelines/preview", json=body()),
              client.post(f"/api/pipelines/{p['id']}/rollback", json={"to_version": 1, "expected_version": 1})):
        assert r.status_code == 409 and r.json()["detail"] == msg


def test_feature_flag_off_returns_404(client, monkeypatch):
    monkeypatch.setenv("GEO_PIPELINES_ENABLED", "false")
    assert client.get("/api/pipelines").status_code == 404
    assert client.post("/api/pipelines", json=body()).status_code == 404
    assert client.post("/api/pipelines/preview", json=body()).status_code == 404


def seed_run(pid, run_id="r1", status="success", items=0):
    with db() as conn:
        conn.execute("INSERT INTO collection_pipeline_runs (run_id,pipeline_id,version,trigger,status,started_at,data_timestamp) "
                     "VALUES (%s,%s,1,'manual',%s,now(),now())", (run_id, pid, status))
        for i in range(items):
            conn.execute("INSERT INTO collection_pipeline_run_items (run_id,pipeline_id,seq,record_id,source_id,source_type,title,data_timestamp) "
                         "VALUES (%s,%s,%s,%s,'s0','rss',%s,now())", (run_id, pid, i, f"rec{run_id}{i}", f"t{i}"))


def test_run_items_pagination(client):
    p = create(client)
    other = create(client, name="另一个")
    seed_run(p["id"], "r1", items=5)
    seed_run(other["id"], "r2", items=1)
    runs = client.get(f"/api/pipelines/{p['id']}/runs").json()
    assert [r["run_id"] for r in runs] == ["r1"]
    r = client.get(f"/api/pipelines/{p['id']}/runs/r1/items?offset=2&limit=2").json()
    assert r["total"] == 5 and [i["title"] for i in r["items"]] == ["t2", "t3"]
    assert client.get(f"/api/pipelines/{p['id']}/runs/r2/items").status_code == 404
    assert client.get(f"/api/pipelines/{p['id']}/runs/r1/items?limit=201").status_code == 422


def test_cross_origin_write_rejected(client):
    r = client.post("/api/pipelines", json=body(), headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/pipelines", json=body(), headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert client.get("/api/pipelines", headers={"Origin": "https://evil.example"}).status_code == 200


def test_run_returns_202_and_runs_in_background(client, monkeypatch):
    p = create(client)
    called, done = {}, threading.Event()

    def fake(pid, *, trigger, actor, **kw):
        called.update(pid=pid, trigger=trigger, actor=actor)
        done.set()
    monkeypatch.setattr(pipeline_routes, "run_pipeline", fake)
    r = client.post(f"/api/pipelines/{p['id']}/run")
    assert r.status_code == 202 and r.json() == {"pipeline_id": p["id"], "accepted": True}
    assert done.wait(5) and called == {"pid": p["id"], "trigger": "manual", "actor": "local"}


def test_run_builtin_uses_run_builtin(client, monkeypatch):
    from backend.pipeline_scheduler import sync_builtin_pipelines
    sync_builtin_pipelines([{"id": "bi1", "name_zh": "内置", "topics": ["usiran"]}])
    pid = client.get("/api/pipelines?kind=builtin").json()["items"][0]["id"]
    done = threading.Event()
    monkeypatch.setattr(pipeline_routes, "run_builtin", lambda *a, **k: done.set())
    assert client.post(f"/api/pipelines/{pid}/run").status_code == 202
    assert done.wait(5)


def test_run_conflicts(client, monkeypatch):
    p = create(client)
    seed_run(p["id"], "r1", status="running")
    r = client.post(f"/api/pipelines/{p['id']}/run")
    assert r.status_code == 409 and r.json()["detail"] == "该管线正在运行"
    q = create(client, name="二")

    def busy():
        raise CollectorBusyError("busy")
    monkeypatch.setattr(pipeline_routes, "_preflight", busy)
    r = client.post(f"/api/pipelines/{q['id']}/run")
    assert r.status_code == 409 and r.json()["detail"] == "已有采集任务在运行，请稍后再试"


def test_run_thread_swallows_exceptions(client, monkeypatch):
    p = create(client)
    done = threading.Event()

    def boom(*a, **k):
        done.set()
        raise RuntimeError("x")
    monkeypatch.setattr(pipeline_routes, "run_pipeline", boom)
    assert client.post(f"/api/pipelines/{p['id']}/run").status_code == 202
    assert done.wait(5)


def test_preview(client, monkeypatch):
    seen = {}

    def fake(row, config, *, limit=5, **kw):
        seen["limit"] = limit
        return {"sources": []}
    monkeypatch.setattr(pipeline_routes, "preview_pipeline", fake)
    r = client.post("/api/pipelines/preview", json={**body(), "limit": 3})
    assert r.status_code == 200 and r.json() == {"sources": []} and seen["limit"] == 3
    assert client.post("/api/pipelines/preview", json={**body(), "limit": 0}).status_code == 422
    assert client.post("/api/pipelines/preview", json={**body(), "limit": 21}).status_code == 422

    def bad(*a, **k):
        raise PipelineError("PREVIEW_FAILED", "试跑失败")
    monkeypatch.setattr(pipeline_routes, "preview_pipeline", bad)
    r = client.post("/api/pipelines/preview", json=body())
    assert r.status_code == 502 and r.json()["detail"] == {"message": "试跑失败"}

    def invalid(*a, **k):
        raise PipelineError("INVALID", "来源无效", {"field": "config.sources"})
    monkeypatch.setattr(pipeline_routes, "preview_pipeline", invalid)
    r = client.post("/api/pipelines/preview", json=body())
    assert r.status_code == 422 and r.json()["detail"]["field"] == "config.sources"


@pytest.fixture
def authed(monkeypatch, tmp_path):
    path = tmp_path / "auth"
    path.write_text("admin:" + sha512_crypt.using(rounds=5000).hash("pw-admin-test") + "\n")
    monkeypatch.setenv("GEO_WEB_AUTH_ENABLED", "true")
    monkeypatch.setenv("GEO_WEB_AUTH_FILE", str(path))
    monkeypatch.setenv("GEOAGENT_ALLOWED_ORIGINS", "https://testserver")
    return path


ADMIN_ONLY = "仅管理员可以管理采集管线"


def _web_client(monkeypatch, sessions):
    from backend import web_auth
    monkeypatch.setattr(web_auth, "session_user", lambda request: sessions.get(request.cookies.get("s")))
    return TestClient(app, base_url="https://testserver")


def test_web_auth_admin_creates_and_owner_recorded(authed, monkeypatch):
    h = {"Origin": "https://testserver"}
    with _web_client(monkeypatch, {"ca": "admin"}) as c:
        c.cookies.set("s", "ca")
        mine = c.post("/api/pipelines", json=body(), headers=h)
        assert mine.status_code == 201 and mine.json()["owner"] == "admin"
        assert c.post(f"/api/pipelines/{mine.json()['id']}/enable", headers=h).status_code == 200
        monkeypatch.setattr(pipeline_routes, "run_pipeline", lambda *a, **k: None)
        assert c.post(f"/api/pipelines/{mine.json()['id']}/run", headers=h).status_code == 202


def test_non_admin_gets_403_on_every_pipeline_route(authed, monkeypatch):
    h = {"Origin": "https://testserver"}
    with _web_client(monkeypatch, {"ca": "admin", "cb": "alice"}) as c:
        c.cookies.set("s", "ca")
        p = c.post("/api/pipelines", json=body(), headers=h).json()
        seed_run(p["id"])
        c.cookies.clear()
        c.cookies.set("s", "cb")
        pid = p["id"]
        calls = [("get", "/api/pipelines", {}), ("get", f"/api/pipelines/{pid}", {}),
                 ("get", f"/api/pipelines/{pid}/versions", {}), ("get", f"/api/pipelines/{pid}/runs", {}),
                 ("get", f"/api/pipelines/{pid}/runs/r1/items", {}),
                 ("post", "/api/pipelines", {"json": body(name="x")}),
                 ("post", "/api/pipelines/preview", {"json": body()}),
                 ("patch", f"/api/pipelines/{pid}", {"json": {"row": {}}, "headers": {"If-Match": "1"}}),
                 ("delete", f"/api/pipelines/{pid}", {}),
                 ("post", f"/api/pipelines/{pid}/enable", {}), ("post", f"/api/pipelines/{pid}/disable", {}),
                 ("post", f"/api/pipelines/{pid}/run", {}),
                 ("post", f"/api/pipelines/{pid}/rollback", {"json": {"to_version": 1, "expected_version": 1}})]
        for method, url, kw in calls:
            kw = {**kw, "headers": {**h, **kw.get("headers", {})}}
            r = getattr(c, method)(url, **kw)
            assert r.status_code == 403 and r.json()["detail"] == ADMIN_ONLY, (method, url, r.status_code, r.text)
        # 管理员未受影响，且非管理员没有改动数据
        c.cookies.clear()
        c.cookies.set("s", "ca")
        assert c.get(f"/api/pipelines/{pid}").json()["enabled"] is False


def test_non_admin_403_precedes_remote_409(authed, monkeypatch):
    monkeypatch.setenv("GEO_COLLECTION_MODE", "remote")
    with _web_client(monkeypatch, {"cb": "alice"}) as c:
        c.cookies.set("s", "cb")
        r = c.post("/api/pipelines", json=body(), headers={"Origin": "https://testserver"})
        assert r.status_code == 403 and r.json()["detail"] == ADMIN_ONLY


def test_unauthenticated_gets_401_and_flag_off_stays_404(authed, monkeypatch):
    with _web_client(monkeypatch, {"cb": "alice"}) as c:
        assert c.get("/api/pipelines").status_code == 401  # 未登录
        c.cookies.set("s", "cb")
        monkeypatch.setenv("GEO_PIPELINES_ENABLED", "false")
        assert c.get("/api/pipelines").status_code == 404  # 开关先于权限


def test_web_login_disabled_local_actor_is_admin(client):
    p = create(client)
    assert p["owner"] == "local"
    assert client.get("/api/pipelines").status_code == 200
    assert client.post(f"/api/pipelines/{p['id']}/enable").status_code == 200


def test_actor_requires_login_when_auth_enabled(authed):
    from starlette.requests import Request
    req = Request({"type": "http", "headers": [], "state": {}})
    with pytest.raises(Exception) as e:
        pipeline_routes.actor_of(req)
    assert e.value.status_code == 401 and e.value.detail == "请先登录"
