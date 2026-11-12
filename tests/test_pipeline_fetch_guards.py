"""管线专用的抓取期防护与过滤：公网主机强制、非 UTF-8 订阅源、回看天数。

这些行为只在管线配置（to_monitor_config 产物）打开；多源采集器自身 config.json 不受影响。
全部离线：本地回环 HTTP 服务器 + 注入的主机检查函数，不访问外网。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collectors" / "geopolitical-source-monitor"


def load_module(name: str):
    if str(COLLECTOR) not in sys.path:
        sys.path.insert(0, str(COLLECTOR))
    spec = importlib.util.spec_from_file_location(f"geo_guard_{name}", COLLECTOR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


web = load_module("web")
feeds = load_module("feeds")
monitor = load_module("geopolitical_source_monitor")

INTRANET = "intranet.test"


def host_problem(host: str):
    return f"域名 {host} 解析到内网或保留地址，已拒绝" if host == INTRANET else None


@pytest.fixture()
def server(monkeypatch):
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            port = self.server.server_address[1]
            redirects = {
                "/to-foreign": "http://evil.example/steal",
                "/to-intranet": f"http://{INTRANET}:{port}/secret",
                "/to-ok": "/ok",
            }
            if self.path in redirects:
                self.send_response(302)
                self.send_header("Location", redirects[self.path])
                self.end_headers()
                return
            body = b"<html><body>ok</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: ANN002
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def client(tmp_path, **kw):
    return web.PolicyHttpClient(user_agent="GuardTest/1.0", sleep=lambda _: None, robots_fetcher=lambda _: "",
                                cache_dir=tmp_path / "cache", host_problem=host_problem, **kw)


SOURCE = {"id": "s", "allowed_hosts": ["127.0.0.1", INTRANET], "max_retries": 0, "rate_limit_seconds": 0,
          "timeout_seconds": 5, "enforce_public_hosts": True}


def test_redirect_inside_allowed_public_hosts_still_followed(server, tmp_path):
    result = client(tmp_path).fetch(server + "/to-ok", SOURCE)
    assert result.status == 200 and result.error_type == "" and result.final_url.endswith("/ok")


@pytest.mark.parametrize("path", ["/to-foreign", "/to-intranet"])
def test_redirect_outside_allowlist_or_to_intranet_blocked(server, tmp_path, path):
    http = client(tmp_path)
    blocked = http.fetch(server + path, SOURCE)
    assert blocked.error_type == "redirect_blocked" and blocked.status == 0 and "跳转被拒绝" in blocked.error_message
    # 只影响该请求：同一个客户端的下一个请求照常
    assert http.fetch(server + "/ok", SOURCE).status == 200


def test_without_flag_default_transport_follows_any_redirect(server, tmp_path):
    """多源采集器自身配置不带 enforce_public_hosts：跳转行为与以前一致（这里只验证不被拦截）。"""
    source = {k: v for k, v in SOURCE.items() if k != "enforce_public_hosts"}
    result = client(tmp_path).fetch(server + "/to-ok", source)
    assert result.status == 200 and result.error_type == ""


def test_target_resolving_to_intranet_rejected_before_connect(tmp_path):
    calls = []

    def transport(request, timeout):
        calls.append(request.full_url)
        return web.TransportResponse(200, {}, b"x", request.full_url)

    result = client(tmp_path, transport=transport).fetch(f"http://{INTRANET}/a", SOURCE)
    assert result.error_type == "host_not_public" and "内网" in result.error_message and calls == []


def test_injected_transport_final_url_outside_allowlist_blocked(tmp_path):
    def transport(request, timeout):
        return web.TransportResponse(200, {}, b"x", "http://10.0.0.1/leak")

    result = client(tmp_path, transport=transport).fetch("http://127.0.0.1/a", SOURCE)
    assert result.error_type == "redirect_blocked"


def _write_config(tmp_path, *, sources, extra=None, request_extra=None):
    base = json.loads((COLLECTOR / "config.json").read_text(encoding="utf-8"))
    config = {**base, "topics": ["usiran"], "topic_terms": {"usiran": base["topic_terms"]["usiran"]},
              "sources": sources, **(extra or {})}
    config["request"] = {**base["request"], "rate_limit_seconds": 0, "max_retries": 0, **(request_extra or {})}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def _run(tmp_path, monkeypatch, config_path, transport):
    monkeypatch.delenv("COLLECTOR_PREVIOUS_OUTPUT_ROOT", raising=False)
    http = client(tmp_path, transport=transport)
    report = monitor.run_collection(config_path, tmp_path / "out", client=http)
    payload = json.loads((tmp_path / "out" / "geo-v6.json").read_text(encoding="utf-8"))
    return report, payload


def test_blocked_article_redirect_only_affects_that_request(tmp_path, monkeypatch):
    listing = ('<html><body><a href="https://pub.example/a/1">Iran and US resume nuclear talks in Oman</a>'
               '<a href="https://pub.example/a/2">Iran missile strike prompts ceasefire talks</a></body></html>')

    def transport(request, timeout):
        url = request.full_url
        if url.endswith("/a/1"):
            return web.TransportResponse(200, {}, b"<html></html>", "http://169.254.169.254/latest")
        if url.endswith("/a/2"):
            html = ("<html><head><title>t</title></head><body><h1>Iran missile strike prompts ceasefire talks</h1>"
                    + "<p>" + "Iran negotiation ceasefire talks continue with missile strike reports. " * 4 + "</p>"
                    + "</body></html>")
            return web.TransportResponse(200, {"Content-Type": "text/html"}, html.encode(), url)
        return web.TransportResponse(200, {"Content-Type": "text/html"}, listing.encode(), url)

    config_path = _write_config(tmp_path, request_extra={"enforce_public_hosts": True}, sources=[
        {"id": "pub", "name_zh": "公网源", "publisher": "公网源", "tier": "T2", "language": "en",
         "allowed_hosts": ["pub.example"], "content_policy": "public_fulltext", "discovery_mode": "listing",
         "entry_urls": ["https://pub.example/list"]}])
    report, payload = _run(tmp_path, monkeypatch, config_path, transport)
    assert report["collection_status"] == "ok"
    access = {r["link"].rsplit("/", 1)[-1]: r["content_access"] for r in payload["records"]}
    assert access == {"1": "title_only", "2": "public_fulltext"}


# ------------------------------------------------------------------ 非 UTF-8 订阅源

def _gb_feed(declared: str) -> bytes:
    xml = (f'<?xml version="1.0" encoding="{declared}"?>'
           "<rss><channel><item><title>伊朗与美国恢复核谈判</title>"
           "<link>https://cn.example/a/1</link><description>阿曼斡旋</description></item>"
           "</channel></rss>")
    return xml.encode("gb18030" if declared.lower() == "gb18030" else declared)


@pytest.mark.parametrize("encoding", ["gb2312", "gbk"])
def test_feed_with_gb_encoding_declaration_parses(encoding):
    body = _gb_feed(encoding)
    text = web._decode(body, {"Content-Type": f"application/rss+xml; charset={encoding}"})
    items = feeds.discover_feed_candidates(text, "https://cn.example/feed", {"allowed_hosts": ["cn.example"]})
    assert [c.title for c in items] == ["伊朗与美国恢复核谈判"]


def test_feed_declared_encoding_decoded_without_charset_header():
    text = web._decode(_gb_feed("gbk"), {})  # 无 charset 时回退解码，文本可能是 windows-1252 乱码
    items = feeds.discover_feed_candidates(text, "https://cn.example/feed", {"allowed_hosts": ["cn.example"]})
    assert len(items) == 1


@pytest.mark.parametrize("declared", ["no-such-codec", "utf-16"])
def test_feed_with_bogus_declaration_never_raises_other_than_feed_parse_error(declared):
    text = f'<?xml version="1.0" encoding="{declared}"?><rss><channel></channel></rss>'
    assert feeds.discover_feed_candidates(text, "https://cn.example/feed", {"allowed_hosts": ["cn.example"]}) == []


def test_undecodable_feed_is_parse_error_and_pipeline_partial(tmp_path, monkeypatch):
    good = ("<rss><channel><item><title>Iran and US resume nuclear talks in Oman</title>"
            "<link>https://good.example/a/1</link><description>sanctions negotiation</description></item>"
            "</channel></rss>").encode()

    def transport(request, timeout):
        if "bad.example" in request.full_url:
            return web.TransportResponse(200, {"Content-Type": "application/rss+xml"},
                                         b"<rss><channel><item><title>\x00\x01</title></item></channel></rss>",
                                         request.full_url)
        return web.TransportResponse(200, {"Content-Type": "application/rss+xml"}, good, request.full_url)

    sources = [{"id": sid, "name_zh": sid, "publisher": sid, "tier": "T2", "language": "en",
                "allowed_hosts": [f"{sid}.example"], "content_policy": "metadata_only", "discovery_mode": "rss",
                "entry_urls": [f"https://{sid}.example/feed"]} for sid in ("good", "bad")]
    report, payload = _run(tmp_path, monkeypatch, _write_config(tmp_path, sources=sources), transport)
    assert report["collection_status"] == "partial"
    assert report["sources"]["bad"]["error"] == "PARSE_ERROR" and report["sources"]["good"]["state"] == "ok"
    assert len(payload["records"]) == 1


# ------------------------------------------------------------------ 回看天数

def _dated_feed(now: datetime) -> bytes:
    def item(n, published):
        pub = f"<pubDate>{published}</pubDate>" if published else ""
        return (f"<item><title>Iran and US resume nuclear talks round {n}</title>"
                f"<link>https://feed.example/a/{n}</link><description>sanctions negotiation</description>{pub}</item>")
    fmt = "%a, %d %b %Y %H:%M:%S +0000"
    return ("<rss><channel>" + item(1, (now - timedelta(days=1)).strftime(fmt))
            + item(2, (now - timedelta(days=30)).strftime(fmt)) + item(3, "") + "</channel></rss>").encode()


def _feed_source():
    return [{"id": "feed", "name_zh": "订阅源", "publisher": "订阅源", "tier": "T2", "language": "en",
             "allowed_hosts": ["feed.example"], "content_policy": "metadata_only", "discovery_mode": "rss",
             "entry_urls": ["https://feed.example/rss"]}]


def _feed_transport(now):
    body = _dated_feed(now)
    return lambda request, timeout: web.TransportResponse(200, {"Content-Type": "application/rss+xml"}, body,
                                                           request.full_url)


def test_lookback_excludes_old_items_only_when_pipeline_flag_set(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    config_path = _write_config(tmp_path, sources=_feed_source(), extra={"lookback_days": 7, "apply_lookback": True})
    report, payload = _run(tmp_path, monkeypatch, config_path, _feed_transport(now))
    assert sorted(r["link"][-1] for r in payload["records"]) == ["1", "3"]  # 缺 published 的不因此排除
    assert report["sources"]["feed"]["excluded"] == 1


def test_lookback_not_applied_for_monitor_own_config(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    config_path = _write_config(tmp_path, sources=_feed_source(), extra={"lookback_days": 7})
    _, payload = _run(tmp_path, monkeypatch, config_path, _feed_transport(now))
    assert sorted(r["link"][-1] for r in payload["records"]) == ["1", "2", "3"]


def test_preview_reports_lookback_reason(tmp_path):
    runner = load_runner()
    now = datetime.now(timezone.utc)
    config_path = _write_config(tmp_path, sources=_feed_source(), extra={"lookback_days": 7, "apply_lookback": True})
    http = runner.PolicyHttpClient(user_agent="t", transport=_feed_transport(now), sleep=lambda _: None,
                                   robots_fetcher=lambda _: "", cache_dir=tmp_path / "c", host_problem=host_problem)
    shown = runner.preview(config_path, 10, client=http)["sources"][0]
    verdicts = {c["url"][-1]: (c["accepted"], c["reason"]) for c in shown["candidates"]}
    assert verdicts["1"] == (True, "") and verdicts["3"] == (True, "")
    assert verdicts["2"] == (False, "超出回看天数")


def load_runner():
    path = ROOT / "collectors" / "pipeline-runner" / "pipeline_runner.py"
    spec = importlib.util.spec_from_file_location("pipeline_runner_guard_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ 未入选原因

def _anchor_only_config(require_category):
    filtering = load_module("filtering")
    base = json.loads((COLLECTOR / "config.json").read_text(encoding="utf-8"))
    config = {**base, "topics": ["usiran"], "require_category": require_category,
              "topic_terms": {"usiran": {"anchors": ["Hormuz"], "strong_anchors": []}}, "exclusion_terms": ["football"]}
    return filtering, config


def test_reason_without_category_requirement_is_missing_anchor():
    filtering, config = _anchor_only_config(False)
    assert filtering.classify_candidate("Weather today", "", "", "en", config)["exclusion_reason"] == "未命中锚词"
    assert filtering.classify_candidate("Hormuz football final", "", "", "en", config)["exclusion_reason"] == "命中排除内容"


def test_reason_with_category_requirement_unchanged():
    filtering, config = _anchor_only_config(True)
    assert (filtering.classify_candidate("Weather today", "", "", "en", config)["exclusion_reason"]
            == "未同时命中主题锚点与事项准入词")
