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
