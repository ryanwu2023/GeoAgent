import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collectors" / "russia-domestic-monitor"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "russia_domestic_retirement", COLLECTOR / "russia_domestic_monitor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_election_only_sources_are_disabled_but_history_bucket_is_preserved():
    config = json.loads((COLLECTOR / "config.json").read_text(encoding="utf-8"))
    sources = {item["id"]: item for item in config["sources"]}
    assert sources["gn-duma"]["enabled"] is False
    assert sources["duma-news"]["enabled"] is False
    assert sources["cikrf-election"]["enabled"] is False
    assert "duma_election" in config["buckets"]


def test_current_report_order_excludes_finished_election_bucket():
    module = load_module()
    config = json.loads((COLLECTOR / "config.json").read_text(encoding="utf-8"))
    assert "duma_election" not in module.active_bucket_order(config)
    assert "oil_exports" in module.active_bucket_order(config)


def test_archived_election_record_can_still_be_classified():
    module = load_module()
    config = json.loads((COLLECTOR / "config.json").read_text(encoding="utf-8"))
    engine = module.Engine(config)
    result = engine.classify(
        "Russia completed its State Duma election on September 20, the commission said",
        "The election result was published after voting ended.",
    )
    assert result is not None
    assert "duma_election" in result["buckets"]

