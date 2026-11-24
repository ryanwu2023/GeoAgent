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
