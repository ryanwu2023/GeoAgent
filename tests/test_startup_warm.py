from backend import app as app_module


def test_warm_startup_precomputes_status_and_views(monkeypatch):
    """新进程第一次 /api/status 要统计整份快照的译文（线上约 11 秒），首页因此卡住；启动时先算好。"""
    calls = []
    monkeypatch.setattr(app_module, "get_working_snapshot_id", lambda: "snap-1")
    monkeypatch.setattr(app_module, "translation_status", lambda sid=None: calls.append(("translation", sid)))
    monkeypatch.setattr(app_module, "snapshots", lambda: calls.append(("snapshots",)))
    monkeypatch.setattr(app_module, "warm_views", lambda sid: calls.append(("views", sid)))

    app_module.warm_startup()

    assert ("translation", None) in calls and ("snapshots",) in calls and ("views", "snap-1") in calls


def test_warm_startup_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(app_module, "get_working_snapshot_id", boom)
    app_module.warm_startup()  # 预热失败不能影响服务启动


def test_refresh_warm_views_covers_both_topics_of_working_snapshot(monkeypatch):
    """缓存一小时无人访问即丢弃；后台定期刷新首页视图，首次打开页面不用等整份快照重算。"""
    calls = []
    monkeypatch.setattr(app_module, "get_working_snapshot_id", lambda: "snap-2")
    monkeypatch.setattr(app_module, "translation_status", lambda sid=None: calls.append(("translation", sid)))
    monkeypatch.setattr(app_module, "workspace", lambda sid, topic, *a: calls.append(("workspace", sid, topic, a)))
    monkeypatch.setattr(app_module, "map_records", lambda sid, topic, *a: calls.append(("map", sid, topic)))

    app_module.refresh_warm_views()

    assert ("translation", None) in calls
    assert {c[:3] for c in calls if c[0] in {"workspace", "map"}} == {
        ("workspace", "snap-2", "usiran"), ("workspace", "snap-2", "ukraine"),
        ("map", "snap-2", "usiran"), ("map", "snap-2", "ukraine")}
    # 与首页请求同参（include_records=false），才能命中同一缓存
    assert all(c[3] == ("", False, "", "") for c in calls if c[0] == "workspace")
