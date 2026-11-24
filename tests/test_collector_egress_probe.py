from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_proxy_probes_require_exact_status():
    """网页网关会把 gstatic 探针转成 200 页面；探活必须要求 204，否则网关被当成代理。"""
    checked = 0
    for path in (ROOT / "collectors").glob("*/*.py"):
        text = path.read_text(encoding="utf-8")
        if "http://www.gstatic.com/generate_204" not in text:
            continue
        checked += 1
        assert '("http://www.gstatic.com/generate_204", 204)' in text, path
        assert "in (200, 204)" not in text, path
    assert checked >= 16


def _load(path):
    import importlib.util
    import sys
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"egress_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path.parent))


def test_managed_run_never_falls_back_to_direct(monkeypatch):
    """线上 Pod 禁止直连：托管运行配了环境代理时，出口池只有环境代理，摘除一次后仍有可用出口。
    以前一次代理超时就回落直连，nyt/bbc 报 Errno 101 Network is unreachable（2026-11-24）。"""
    monkeypatch.setenv("GEO_COLLECTOR_MANAGED", "true")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:18080")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:18080")
    for name in ("GEO_PROXY", "COLLECTOR_PROXY", "MONITOR_PROXY"):
        monkeypatch.delenv(name, raising=False)
    checked = 0
    for path in sorted((ROOT / "collectors").glob("*/*.py")):
        if "http://www.gstatic.com/generate_204" not in path.read_text(encoding="utf-8"):
            continue
        module = _load(path)
        live = getattr(module, "live_proxies", None) or getattr(module, "live_egress")
        demote = getattr(module, "demote_proxy", None) or getattr(module, "demote_egress", None)
        pool = live()
        assert pool == ["http://127.0.0.1:18080"] * 2, path
        if demote:
            demote(pool[0])
        assert live() == ["http://127.0.0.1:18080"] * 2, path
        checked += 1
    assert checked >= 16
