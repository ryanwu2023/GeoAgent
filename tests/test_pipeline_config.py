from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

from backend.pipeline_config import (
    PipelineConfigError,
    next_run_at,
    to_monitor_config,
    validate_pipeline,
)

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collectors" / "geopolitical-source-monitor"
BASE = json.loads((COLLECTOR / "config.json").read_text(encoding="utf-8"))

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731

ROW = {"name": "测试管线", "description": "", "topic": "usiran", "interval_sec": 3600,
       "daily_times": None, "timezone": "Asia/Shanghai"}
CFG = {
    "schema_version": "pipeline/1",
    "sources": [
        {"id": "reuters-world-rss", "type": "rss", "name": "路透世界", "feed_url": "https://news.example/f",
         "allowed_hosts": ["news.example"], "language": "en", "tier": "T2", "content_policy": "metadata_only"},
        {"id": "state-press", "type": "web", "name": "美国国务院", "entry_urls": ["https://www.state.gov/releases/"],
         "allowed_hosts": ["www.state.gov"], "language": "en", "tier": "T1",
         "content_policy": "public_fulltext", "link_include_pattern": "/releases/"},
    ],
    "filters": {"include_any": ["Iran"], "include_strong": ["Hormuz"], "exclude": ["sports"]},
    "request": {},
}


def cfg(**patch):
    c = copy.deepcopy(CFG)
    c.update(patch)
    return c


def row(**patch):
    r = copy.deepcopy(ROW)
    r.update(patch)
    return r


def cfg_with(feed_url, hosts=None):
    c = copy.deepcopy(CFG)
    c["sources"][0]["feed_url"] = feed_url
    if hosts:
        c["sources"][0]["allowed_hosts"] = hosts
    return c


def fail(r, c, resolve=PUBLIC):
    with pytest.raises(PipelineConfigError) as e:
        validate_pipeline(r, c, resolve=resolve)
    return e.value


def test_valid_config_fills_defaults():
    r, c = validate_pipeline(ROW, CFG, resolve=PUBLIC)
    assert r["name"] == "测试管线" and r["interval_sec"] == 3600 and r["daily_times"] is None
    assert c["filters"]["require_category"] is True and c["filters"]["lookback_days"] == 14
    assert c["filters"]["max_candidates_per_source"] == 80
    assert c["request"] == {"timeout_seconds": 25, "rate_limit_seconds": 1.5, "max_pages_per_source": 3}


def test_rejects_url_outside_allowed_hosts():
    e = fail(ROW, cfg_with("https://evil.example/f"))
    assert e.field == "sources[0].feed_url"


def test_rejects_bad_scheme():
    assert fail(ROW, cfg_with("file:///etc/passwd")).field == "sources[0].feed_url"
    assert fail(ROW, cfg_with("ftp://news.example/f")).field == "sources[0].feed_url"


def test_rejects_host_resolving_to_private_ip():
    e = fail(ROW, cfg_with("https://news.example/f"), lambda h: ["10.1.2.3"])
    assert "内网" in e.message


@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "240.0.0.1"])
def test_rejects_special_addresses(ip):
    e = fail(ROW, cfg_with("https://news.example/f"), lambda h: ["93.184.216.34", ip])
    assert "内网" in e.message


def test_rejects_ip_literal_private():
    e = fail(ROW, cfg_with("http://127.0.0.1/f", ["127.0.0.1"]), PUBLIC)
    assert "内网" in e.message


def test_rejects_unresolvable():
    def boom(h):
        raise OSError("nope")
    assert "无法解析域名" in fail(ROW, CFG, boom).message


def test_rejects_unknown_topic():
    assert fail(row(topic="taiwan"), CFG).field == "row.topic"


def test_rejects_interval_and_daily_times_together():
    e = fail(row(interval_sec=3600, daily_times=["08:00"]), CFG)
    assert e.field in ("row.interval_sec", "row.daily_times")


@pytest.mark.parametrize("v", [599, 604801, 0, -5, "x", True])
def test_rejects_bad_interval(v):
    assert fail(row(interval_sec=v), CFG).field == "row.interval_sec"


def test_interval_bounds_ok():
    for v in (600, 604800):
        validate_pipeline(row(interval_sec=v), CFG, resolve=PUBLIC)


def test_daily_times_normalized_and_validated():
    r, _ = validate_pipeline(row(interval_sec=None, daily_times=["18:30", "08:00", "08:00"]), CFG, resolve=PUBLIC)
    assert r["daily_times"] == ["08:00", "18:30"]
    for bad in (["24:00"], ["8:00"], ["12:60"], ["ab"], []):
        assert fail(row(interval_sec=None, daily_times=bad), CFG).field == "row.daily_times"
    many = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
    assert fail(row(interval_sec=None, daily_times=many), CFG).field == "row.daily_times"


def test_manual_only_allowed():
    r, _ = validate_pipeline(row(interval_sec=None, daily_times=None), CFG, resolve=PUBLIC)
    assert next_run_at(r, datetime(2026, 11, 11, tzinfo=UTC)) is None


def test_timezone_and_name_rules():
    assert fail(row(timezone="Mars/Base"), CFG).field == "row.timezone"
    assert fail(row(name="  "), CFG).field == "row.name"
    assert fail(row(name="x" * 101), CFG).field == "row.name"
    assert fail(row(description="x" * 2001), CFG).field == "row.description"
    r, _ = validate_pipeline(row(name="  好  "), CFG, resolve=PUBLIC)
    assert r["name"] == "好"


def test_limits():
    many = [dict(CFG["sources"][0], id=f"s{i}") for i in range(31)]
    assert fail(ROW, cfg(sources=many)).field == "sources"
    c = copy.deepcopy(CFG)
    c["filters"]["include_any"] = [f"w{i}" for i in range(201)]
    assert fail(ROW, c).field.startswith("filters")
    c = copy.deepcopy(CFG)
    c["filters"].update(include_any=[f"a{i}" for i in range(100)], include_strong=[f"b{i}" for i in range(60)],
                        exclude=[f"c{i}" for i in range(41)])
    assert fail(ROW, c).field.startswith("filters")
    c = copy.deepcopy(CFG)
    c["request"]["max_pages_per_source"] = 6
    assert fail(ROW, c).field == "request.max_pages_per_source"
    c = copy.deepcopy(CFG)
    c["sources"][1]["link_include_pattern"] = "a" * 201
    assert fail(ROW, c).field == "sources[1].link_include_pattern"


def test_rejects_invalid_regex():
    c = copy.deepcopy(CFG)
    c["sources"][1]["link_include_pattern"] = "(["
    assert fail(ROW, c).field == "sources[1].link_include_pattern"


def test_source_rules():
    c = copy.deepcopy(CFG)
    c["sources"][1]["id"] = c["sources"][0]["id"]
    assert fail(ROW, c).field == "sources[1].id"
    for key, bad in (("id", "Bad_ID"), ("tier", "T9"), ("content_policy", "x"), ("type", "search"),
                     ("allowed_hosts", []), ("allowed_hosts", ["WWW.State.gov"])):
        c = copy.deepcopy(CFG)
        c["sources"][1][key] = bad
        assert fail(ROW, c).field == f"sources[1].{key}"
    c = copy.deepcopy(CFG)
    del c["sources"][0]["feed_url"]
    assert fail(ROW, c).field == "sources[0].feed_url"
    c = copy.deepcopy(CFG)
    c["sources"][1]["entry_urls"] = []
    assert fail(ROW, c).field == "sources[1].entry_urls"
    assert fail(ROW, cfg(sources=[])).field == "sources"


def test_unknown_keys_rejected():
    assert fail(ROW, cfg(extra=1)).field == "extra"
    c = copy.deepcopy(CFG)
    c["sources"][0]["feeed_url"] = "x"
    assert fail(ROW, c).field == "sources[0].feeed_url"
    c = copy.deepcopy(CFG)
    c["filters"]["bogus"] = 1
    assert fail(ROW, c).field == "filters.bogus"
    c = copy.deepcopy(CFG)
    c["request"]["bogus"] = 1
    assert fail(ROW, c).field == "request.bogus"


def test_to_monitor_config_maps_terms_and_rss():
    r, c = validate_pipeline(ROW, CFG, resolve=PUBLIC)
    out = to_monitor_config(r, c, BASE)
    assert out["topics"] == ["usiran"]
    assert out["topic_terms"] == {"usiran": {"anchors": ["Iran", "Hormuz"], "strong_anchors": ["Hormuz"]}}
    assert out["categories"] == BASE["categories"]
    assert out["exclusion_terms"] == BASE["exclusion_terms"] + ["sports"]
    assert out["require_category"] is True
    s0, s1 = out["sources"]
    assert s0["discovery_mode"] == "rss" and s0["entry_urls"] == [c["sources"][0]["feed_url"]]
    assert s1["discovery_mode"] == "listing" and s1["link_include_pattern"] == "/releases/"
    assert s0["publisher"] == s0["name_zh"] == "路透世界"
    assert out["lookback_days"] == 14 and out["request"]["max_pages_per_source"] == 3
    assert BASE["topics"] == ["usiran", "ukraine"]  # base untouched


def test_to_monitor_config_passes_filtering_load_config(tmp_path):
    sys.path.insert(0, str(COLLECTOR))
    import filtering
    r, c = validate_pipeline(ROW, CFG, resolve=PUBLIC)
    p = tmp_path / "c.json"
    p.write_text(json.dumps(to_monitor_config(r, c, BASE), ensure_ascii=False), encoding="utf-8")
    assert filtering.load_config(p)["topics"] == ["usiran"]


def test_next_run_interval():
    after = datetime(2026, 11, 11, 1, 0, tzinfo=UTC)
    assert next_run_at({"interval_sec": 600}, after) == datetime(2026, 11, 11, 1, 10, tzinfo=UTC)


def test_next_run_daily_times_crosses_midnight():
    r = {"daily_times": ["08:00"], "timezone": "Asia/Shanghai", "interval_sec": None}
    assert next_run_at(r, datetime(2026, 11, 11, 1, 0, tzinfo=UTC)) == datetime(2026, 11, 12, 0, 0, tzinfo=UTC)
    assert next_run_at(r, datetime(2026, 11, 10, 23, 0, tzinfo=UTC)) == datetime(2026, 11, 11, 0, 0, tzinfo=UTC)


def test_next_run_strictly_later():
    r = {"daily_times": ["08:00", "20:00"], "timezone": "Asia/Shanghai", "interval_sec": None}
    assert next_run_at(r, datetime(2026, 11, 11, 0, 0, tzinfo=UTC)) == datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
