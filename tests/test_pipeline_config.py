from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

from backend.pipeline_config import (
    PipelineConfigError,
    to_monitor_config,
    validate_pipeline,
    validate_row,
)

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collectors" / "geopolitical-source-monitor"
BASE = json.loads((COLLECTOR / "config.json").read_text(encoding="utf-8"))

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731

ROW = {"name": "测试管线", "description": "", "topics": ["usiran"]}
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
    assert r == {"name": "测试管线", "description": "", "topics": ["usiran"]}
    assert c["filters"]["require_category"] is True and c["filters"]["lookback_days"] == 14
    assert c["filters"]["max_candidates_per_source"] == 80
    assert c["request"] == {"timeout_seconds": 25, "rate_limit_seconds": 1.5}


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
    assert fail(row(topics=["taiwan"]), CFG).field == "row.topics"


def test_row_has_no_schedule_fields():
    """单独定时已取消：行字段只接受 name/description/topics，传调度字段或其它未知字段 → 校验失败。"""
    assert validate_row(ROW) == {"name": "测试管线", "description": "", "topics": ["usiran"]}
    for key, value in (("interval_sec", 3600), ("daily_times", ["08:00"]), ("timezone", "UTC"), ("extra", "x")):
        with pytest.raises(PipelineConfigError) as e:
            validate_row(row(**{key: value}))
        assert e.value.field == f"row.{key}"
        assert fail(row(**{key: value}), CFG).field == f"row.{key}"


def test_rejects_cgnat_literal_and_resolved():
    e = fail(ROW, cfg_with("http://100.100.100.200/f", ["100.100.100.200"]), PUBLIC)
    assert "内网" in e.message
    assert "内网" in fail(ROW, CFG, lambda h: ["100.64.0.1"]).message


@pytest.mark.parametrize("host", ["a..b.com", "a" * 64 + ".com", "-a.com", "a-.com", ("a" * 60 + ".") * 5 + "com"])
def test_rejects_malformed_allowed_host(host):
    c = copy.deepcopy(CFG)
    c["sources"][0]["allowed_hosts"] = [host]
    assert fail(ROW, c).field == "sources[0].allowed_hosts"


def test_unicode_error_from_resolver_is_unresolvable():
    def boom(h):
        raise UnicodeError("label too long")
    assert "无法解析域名" in fail(ROW, CFG, boom).message


def test_name_rules():
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
    c["request"]["max_pages_per_source"] = 3  # 翻页项已移除（未实现），按未知字段拒绝
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
    assert out["lookback_days"] == 14 and "max_pages_per_source" not in out["request"]
    # 只有管线配置开启公网主机强制与回看过滤；多源采集器自己的 config.json 不带这两个开关
    assert out["request"]["enforce_public_hosts"] is True and out["apply_lookback"] is True
    assert "enforce_public_hosts" not in BASE["request"] and "apply_lookback" not in BASE
    assert BASE["topics"] == ["usiran", "ukraine"]  # base untouched


def test_to_monitor_config_passes_filtering_load_config(tmp_path, monkeypatch):
    import importlib.util
    monkeypatch.syspath_prepend(str(COLLECTOR))
    spec = importlib.util.spec_from_file_location("filtering_for_pipeline_cfg_test", COLLECTOR / "filtering.py")
    filtering = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(filtering)
    r, c = validate_pipeline(ROW, CFG, resolve=PUBLIC)
    p = tmp_path / "c.json"
    p.write_text(json.dumps(to_monitor_config(r, c, BASE), ensure_ascii=False), encoding="utf-8")
    assert filtering.load_config(p)["topics"] == ["usiran"]


def test_topics_multi_select_validated():
    r, _ = validate_pipeline(row(topics=["ukraine", "usiran", "usiran"]), CFG, resolve=PUBLIC)
    assert r["topics"] == ["usiran", "ukraine"] and "topic" not in r
    for bad in ([], ["taiwan"], ["usiran", "taiwan"], "usiran", None):
        assert fail(row(topics=bad), CFG).field == "row.topics"


def test_to_monitor_config_multi_topics_and_hints():
    r, c = validate_pipeline(row(topics=["ukraine", "usiran"]), CFG, resolve=PUBLIC)
    out = to_monitor_config(r, c, BASE)
    shared = {"anchors": ["Iran", "Hormuz"], "strong_anchors": ["Hormuz"]}
    assert out["topics"] == ["usiran", "ukraine"]
    assert out["topic_terms"] == {"usiran": shared, "ukraine": shared}
    assert out["topic_hints"] == {t: BASE["topic_terms"][t]["anchors"] for t in ("usiran", "ukraine")}
    assert "topic_hints" not in BASE


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_rejects_allowed_host_that_is_internal_even_if_unused(host):
    """allowed_hosts 每一项都要解析检查：未被任何 URL 使用的内网主机也会让抓取期跳转到内网。"""
    c = copy.deepcopy(CFG)
    c["sources"][1]["allowed_hosts"] = ["www.state.gov", host]
    resolve = lambda h: ["127.0.0.1"] if h == "localhost" else ["93.184.216.34"]  # noqa: E731
    e = fail(ROW, c, resolve)
    assert e.field == "sources[1].allowed_hosts[1]" and "内网" in e.message


def test_rejects_allowed_host_resolving_to_10_net():
    c = copy.deepcopy(CFG)
    c["sources"][0]["allowed_hosts"] = ["news.example", "intranet.example"]
    resolve = lambda h: ["10.0.0.8"] if h == "intranet.example" else ["93.184.216.34"]  # noqa: E731
    e = fail(ROW, c, resolve)
    assert e.field == "sources[0].allowed_hosts[1]" and "内网" in e.message


def test_rejects_unresolvable_allowed_host():
    c = copy.deepcopy(CFG)
    c["sources"][0]["allowed_hosts"] = ["news.example", "gone.example"]

    def resolve(h):
        if h == "gone.example":
            raise OSError("nxdomain")
        return ["93.184.216.34"]
    e = fail(ROW, c, resolve)
    assert e.field == "sources[0].allowed_hosts[1]" and "无法解析域名" in e.message
