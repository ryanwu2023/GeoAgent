import json
import subprocess
import sys

import pytest

from backend import builtin_config
from backend.builtin_config import BuiltinConfigError, apply_overlay, build_overlay, term_groups, with_overrides
from backend.paths import project_root


def public(host):
    return ["93.184.216.34"]


def test_every_editable_collector_has_sources_and_term_groups():
    for cid in builtin_config.EDITABLE:
        config = builtin_config.base_config(cid)
        assert config["sources"] and term_groups(config), cid
        assert apply_overlay(config, {}) == config, cid
    assert not builtin_config.editable("taco-monitor") and not builtin_config.editable("xinhua-monitor")


def test_overlay_survives_code_changes():
    """BC-09：代码新增的来源照常进入；代码删掉的来源和词表路径，其覆盖项被忽略。"""
    base = {"sources": [{"id": "a", "publisher": "A", "url": "https://a.example/rss"},
                        {"id": "b", "publisher": "B", "url": "https://b.example/rss"}],
            "buckets": {"x": {"label": "X", "require_all": [["iran"], ["strike"]]}}}
    overlay = {"sources": {"set": {"a": {"url": "https://a2.example/rss"}, "gone": {"url": "https://g.example"}},
                           "remove": ["b"], "add": [{"id": "c", "publisher": "C", "url": "https://c.example/rss"}]},
               "terms": {"buckets/x/require_all/0": ["iran", "tehran"], "buckets/gone/require_all/0": ["x"]}}
    code_update = {**base, "sources": [*base["sources"], {"id": "d", "publisher": "D", "url": "https://d.example/rss"}]}
    merged = apply_overlay(code_update, overlay)
    assert [s["id"] for s in merged["sources"]] == ["a", "d", "c"]
    assert merged["sources"][0]["url"] == "https://a2.example/rss"
    assert merged["buckets"]["x"]["require_all"] == [["iran", "tehran"], ["strike"]]
    assert base["sources"][0]["url"] == "https://a.example/rss"  # 不改入参


def test_term_group_labels_use_chinese_bucket_names():
    groups = term_groups(builtin_config.base_config("mideast-monitor"))
    first = groups[0]
    assert first["label"].startswith("分桶 · ") and first["path"].startswith("buckets/")
    assert not any(g["path"].startswith(("sources", "report", "glossary")) for g in groups)


def test_tiered_collector_rejects_unknown_tier():
    base = builtin_config.base_config("mideast-monitor")
    rows = [builtin_config.source_view(s) for s in base["sources"]]
    rows[0]["tier"] = "T9"
    with pytest.raises(BuiltinConfigError) as e:
        build_overlay("mideast-monitor", {"sources": rows}, resolve=public)
    assert e.value.field == "sources[0].tier"
    assert build_overlay("mideast-monitor", {"sources": [builtin_config.source_view(s) for s in base["sources"]]},
                         resolve=public) == {}


def test_with_overrides_writes_merged_config_and_passes_path(tmp_path):
    registry = [{"id": "naval-monitor", "env": {"KEEP": "1"}}, {"id": "taco-monitor"}]
    overlay = {"terms": {"relevance_keywords": ["carrier"]}}
    result = with_overrides(registry, root=tmp_path, overlays={"naval-monitor": overlay, "taco-monitor": overlay})
    path = tmp_path / "naval-monitor" / "state" / "config.override.json"
    assert result[0]["env"] == {"KEEP": "1", "GEO_COLLECTOR_CONFIG": str(path)}
    assert json.loads(path.read_text(encoding="utf-8"))["relevance_keywords"] == ["carrier"]
    assert result[1] == {"id": "taco-monitor"}  # 不可编辑的采集器不传覆盖
    assert registry[0]["env"] == {"KEEP": "1"}


def test_with_overrides_falls_back_when_database_unavailable(monkeypatch):
    """BC-11：读不到覆盖层时按代码配置运行。"""
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(builtin_config, "_current_overlays", boom)
    registry = [{"id": "naval-monitor"}]
    assert with_overrides(registry) is registry


def test_collector_reads_override_path_from_environment(tmp_path):
    """BC-03：采集器进程用 GEO_COLLECTOR_CONFIG 指向的合并配置。"""
    override = tmp_path / "config.json"
    override.write_text(json.dumps({"marker": "override"}), encoding="utf-8")
    script = ("import importlib.util,sys;sys.argv=['x'];"
              "spec=importlib.util.spec_from_file_location('m',sys.argv[0] if False else r'%s');"
              "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);print(m.CFG_PATH)")
    path = project_root() / "collectors" / "mideast-monitor" / "mideast_monitor.py"
    out = subprocess.run([sys.executable, "-c", script % path], capture_output=True, text=True,
                         env={"GEO_COLLECTOR_CONFIG": str(override), "PATH": "/usr/bin:/bin"}, timeout=60)
    assert out.stdout.strip() == str(override), out.stderr[-500:]
