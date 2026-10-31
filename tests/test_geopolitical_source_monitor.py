from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collectors" / "geopolitical-source-monitor"


def load_module(name: str):
    path = COLLECTOR / f"{name}.py"
    if str(COLLECTOR) not in sys.path:
        sys.path.insert(0, str(COLLECTOR))
    spec = importlib.util.spec_from_file_location(f"geo_source_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_source_config_has_twenty_unique_compliant_sources():
    filtering = load_module("filtering")
    config = filtering.load_config(COLLECTOR / "config.json")
    sources = config["sources"]
    assert len(sources) == 20
    assert len({item["id"] for item in sources}) == 20
    assert all(item["allowed_hosts"] for item in sources)
    assert {item["content_policy"] for item in sources} <= {"public_fulltext", "metadata_only"}
    serialized = json.dumps(config, ensure_ascii=False).lower()
    assert "taiwan" not in serialized
    assert "台海" not in serialized
    assert set(config["topics"]) == {"usiran", "ukraine"}


def test_topic_gate_accepts_business_relevant_examples_and_maps_dimensions():
    filtering = load_module("filtering")
    config = filtering.load_config(COLLECTOR / "config.json")
    cases = [
        ("CENTCOM deploys additional forces near Iran", "missile defense readiness", "", "en", "usiran", "us_readiness"),
        ("Iran Revolutionary Guard begins drone exercise", "IRGC missile units participate", "", "en", "usiran", "iran_readiness"),
        ("Oman mediates renewed US-Iran talks", "ceasefire proposal discussed", "", "en", "usiran", "diplomacy"),
        ("Iran inflation accelerates as rial weakens", "food prices and unemployment rise", "", "en", "usiran", "iran_domestic"),
        ("Houthi missile attack disrupts Red Sea shipping", "tanker insurance costs rise", "", "en", "usiran", "regional"),
        ("Russian missile strike hits Ukraine energy infrastructure", "civilian casualties reported in Kyiv", "", "en", "ukraine", "frontline"),
        ("Germany announces new military aid for Ukraine", "weapons delivery includes air defense ammunition", "", "en", "ukraine", "aid"),
        ("Russia fiscal deficit widens as defense spending rises", "oil export revenue falls", "", "en", "ukraine", "russia_domestic"),
        ("EU leaders debate continued Ukraine assistance", "election pressure may limit aid", "", "en", "ukraine", "eu_domestic"),
        ("Russia and Ukraine resume ceasefire negotiations", "delegations discuss an agreement", "", "en", "ukraine", "diplomacy"),
    ]
    for title, summary, body, language, topic, dimension in cases:
        result = filtering.classify_candidate(title, summary, body, language, config)
        assert result["accepted"], (title, result)
        assert topic in result["topics"]
        assert result["dimensions"][topic] == dimension
        assert result["matched_discovery_terms"]
        assert result["matched_admission_terms"]


def test_topic_gate_rejects_pollution_wide_terms_and_taiwan_scope():
    filtering = load_module("filtering")
    config = filtering.load_config(COLLECTOR / "config.json")
    rejected = [
        ("Iran wins international volleyball championship", "The team celebrated its sports victory"),
        ("Russian ballet company opens cultural festival", "Performances continue this week"),
        ("United States releases monthly tourism statistics", "Visitor arrivals increased"),
        ("Israel technology company reports quarterly earnings", "Revenue increased"),
        ("PLA begins military exercise around Taiwan", "Warships enter the Taiwan Strait"),
        ("China and Russia expand ordinary consumer trade", "Retail goods sales increased"),
    ]
    for title, summary in rejected:
        result = filtering.classify_candidate(title, summary, "", "en", config)
        assert not result["accepted"], (title, result)
        assert not result["topics"]


class FakeTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_policy_http_client_honors_hosts_robots_retries_and_conditional_cache():
    web = load_module("web")
    source = {
        "id": "sample",
        "allowed_hosts": ["example.com"],
        "rate_limit_seconds": 0,
        "timeout_seconds": 2,
        "max_retries": 2,
    }
    transport = FakeTransport([
        web.TransportResponse(429, {"Retry-After": "0"}, b"busy", "https://example.com/news"),
        web.TransportResponse(200, {"ETag": '"v1"', "Content-Type": "text/html; charset=utf-8"}, b"<h1>Iran missile strike</h1>", "https://example.com/news"),
        web.TransportResponse(304, {}, b"", "https://example.com/news"),
    ])
    client = web.PolicyHttpClient(
        user_agent="GeoAgentTest/1.0",
        transport=transport,
        sleep=lambda _: None,
        robots_fetcher=lambda _: "User-agent: *\nDisallow: /private\n",
    )
    assert client.fetch("https://not-example.com/news", source).error_type == "host_not_allowed"
    assert client.fetch("https://example.com/private/report", source).error_type == "robots_disallowed"
    first = client.fetch("https://example.com/news", source)
    second = client.fetch("https://example.com/news", source)
    assert first.status == 200 and first.body.startswith("<h1>")
    assert second.status == 304 and second.body == first.body and second.from_cache
    assert len(transport.requests) == 3
    assert transport.requests[-1][0].headers["If-none-match"] == '"v1"'


def test_policy_http_client_does_not_retry_forbidden_response():
    web = load_module("web")
    forbidden = HTTPError("https://example.com/a", 403, "Forbidden", {}, None)
    transport = FakeTransport([forbidden])
    client = web.PolicyHttpClient(
        user_agent="GeoAgentTest/1.0",
        transport=transport,
        sleep=lambda _: None,
        robots_fetcher=lambda _: "User-agent: *\nAllow: /\n",
    )
    source = {"id": "sample", "allowed_hosts": ["example.com"], "rate_limit_seconds": 0, "max_retries": 3}
    result = client.fetch("https://example.com/a", source)
    assert result.status == 403
    assert result.error_type == "http_403"
    assert len(transport.requests) == 1


def test_discovery_and_article_parser_distinguish_fulltext_and_restricted_pages():
    web = load_module("web")
    source = {"id": "sample", "allowed_hosts": ["example.com"], "content_policy": "public_fulltext"}
    listing = """
      <html><head><script type="application/ld+json">
      {"@type":"ItemList","itemListElement":[{"url":"/json-story","name":"Russia and Ukraine resume ceasefire talks"}]}
      </script></head><body>
      <a href="/story?utm_source=test"><span>Iran missile strike raises regional risks</span></a>
      <a href="https://elsewhere.example/news">External item</a>
      </body></html>
    """
    candidates = web.discover_candidates(listing, "https://example.com/world", source)
    assert {item.url for item in candidates} == {
        "https://example.com/json-story",
        "https://example.com/story",
    }
    full = """
      <html><head>
      <meta property="og:title" content="Iran and US resume nuclear talks">
      <meta name="description" content="Oman mediates negotiations over sanctions.">
      <meta property="article:published_time" content="2026-10-31T08:00:00Z">
      </head><body><article><h1>Iran and US resume nuclear talks</h1>
      <p>Officials said the delegations discussed a ceasefire framework and sanctions relief in detail.</p>
      <p>The statement did not confirm that a final agreement had been reached by either side.</p>
      </article></body></html>
    """
    article = web.parse_public_article(full, "https://example.com/story", source)
    assert article.title == "Iran and US resume nuclear talks"
    assert article.published == "2026-10-31T08:00:00Z"
    assert article.content_access == "public_fulltext"
    assert "final agreement" in article.body
    restricted = web.parse_public_article(
        '<html><head><meta property="og:title" content="Iran update"><meta name="description" content="Public summary"></head><body>Please sign in to continue your subscription</body></html>',
        "https://example.com/paywall",
        source,
    )
    assert restricted.title == "Iran update"
    assert restricted.summary == "Public summary"
    assert restricted.body == ""
    assert restricted.content_access == "restricted_metadata_only"
    empty = web.parse_public_article("<html><body></body></html>", "https://example.com/empty", source)
    assert empty.error_type == "parse_error"


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch(self, url, source):
        self.calls.append((url, source["id"]))
        return self.responses[url]


def test_collection_isolates_source_failures_and_writes_auditable_outputs(tmp_path):
    filtering = load_module("filtering")
    web = load_module("web")
    monitor = load_module("geopolitical_source_monitor")
    config = filtering.load_config(COLLECTOR / "config.json")
    config["request"].update({"rate_limit_seconds": 0, "max_candidates_per_source": 10})
    config["sources"] = [
        {
            "id": "full", "name_zh": "官方全文", "publisher": "Official", "tier": "T1", "language": "en",
            "entry_urls": ["https://full.example/list"], "allowed_hosts": ["full.example"],
            "discovery_mode": "listing", "content_policy": "public_fulltext",
        },
        {
            "id": "meta", "name_zh": "受限媒体", "publisher": "Media", "tier": "T3", "language": "en",
            "entry_urls": ["https://meta.example/list"], "allowed_hosts": ["meta.example"],
            "discovery_mode": "public_metadata", "content_policy": "metadata_only",
        },
        {
            "id": "failed", "name_zh": "失败来源", "publisher": "Failed", "tier": "T1", "language": "en",
            "entry_urls": ["https://failed.example/list"], "allowed_hosts": ["failed.example"],
            "discovery_mode": "listing", "content_policy": "public_fulltext",
        },
    ]
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    listing_full = '<a href="/story?utm_source=list">Iran missile strike raises regional conflict risk</a>'
    listing_meta = '<a href="/item">Russia sanctions restrict oil exports during Ukraine war</a>'
    article = """
      <html><head><meta property="og:title" content="Iran missile strike raises regional conflict risk">
      <meta name="description" content="An official statement reports a new military operation.">
      <meta property="article:published_time" content="2026-10-31T08:00:00Z"></head>
      <body><article><p>Officials said the missile strike formed part of a wider military operation near Iran and the Persian Gulf.</p>
      <p>The statement records the issuing authority's account and does not provide independent verification of damage.</p></article></body></html>
    """
    responses = {
        "https://full.example/list": web.FetchResult(200, listing_full, "https://full.example/list", {}),
        "https://full.example/story": web.FetchResult(200, article, "https://full.example/story", {}),
        "https://meta.example/list": web.FetchResult(200, listing_meta, "https://meta.example/list", {}),
        "https://failed.example/list": web.FetchResult(403, "", "https://failed.example/list", {}, "http_403"),
    }
    output = tmp_path / "output"
    report = monitor.run_collection(config_path, output, FakeClient(responses))
    assert report["collection_status"] == "partial"
    assert report["records"] == 2
    assert report["sources"]["full"]["state"] == "ok"
    assert report["sources"]["meta"]["state"] == "ok"
    assert report["sources"]["failed"]["state"] == "failed"
    payload = json.loads((output / "geo-v6.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "geo.v6/1"
    assert {item["content_access"] for item in payload["records"]} == {"public_fulltext", "public_metadata"}
    assert all(item["topics"] for item in payload["records"])
    assert all("taiwan" not in json.dumps(item).lower() for item in payload["records"])
    source_status = json.loads((output / "_source_status.json").read_text(encoding="utf-8"))
    assert source_status["collection_status"] == "partial"
    assert len(source_status["sources"]) == 3
    assert list((output / "raw").rglob("*.html"))


def test_merge_records_revalidates_history_and_merges_aliases():
    filtering = load_module("filtering")
    monitor = load_module("geopolitical_source_monitor")
    config = filtering.load_config(COLLECTOR / "config.json")
    polluted = {
        "id": "old-sport", "title": "Iran wins volleyball championship", "summary": "Sports victory",
        "link": "https://old.example/sport", "published": "2026-10-31", "source_id": "old", "topics": ["usiran"],
        "dimensions": {"usiran": "regional"},
    }
    first = {
        "id": "a", "title": "Russia launches missile strike on Ukraine", "summary": "Civilian casualties reported",
        "link": "https://one.example/story?utm_source=x", "published": "2026-10-31T01:00:00Z", "source_id": "wire-a",
        "topics": ["ukraine"], "dimensions": {"ukraine": "frontline"}, "content_access": "public_summary",
    }
    mirror = {
        **first, "id": "b", "link": "https://two.example/mirror", "source_id": "wire-b", "content_access": "public_fulltext",
        "summary": "Civilian casualties reported after the missile strike; independent verification remains pending.",
    }
    merged = monitor.merge_records([polluted], [first, mirror], config)
    assert len(merged) == 1
    assert merged[0]["content_access"] == "public_fulltext"
    assert set(merged[0]["aliases"]) == {"https://one.example/story", "https://two.example/mirror"}
    assert {item["source_id"] for item in merged[0]["source_mentions"]} == {"wire-a", "wire-b"}

