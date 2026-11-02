import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(collector: str, module_name: str):
    folder = ROOT / "collectors" / collector
    spec = importlib.util.spec_from_file_location(collector.replace("-", "_"), folder / module_name)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    return module, config


def test_diplomacy_distinguishes_received_letter_from_formal_reply():
    module, config = _load("diplomacy-monitor", "diplomacy_monitor.py")
    engine = module.Engine(config)
    received = engine.classify(
        "Rezaei says he reviewed Vance letter but has not responded",
        "Qatar relayed the US proposal to Iran; Tehran still insists on seven conditions.",
    )
    assert received is not None
    assert "proposal_exchange" in received["buckets"]
    assert received["flags"]["letter_received"] is True
    assert received["flags"]["formal_reply_pending"] is True
    assert received["flags"]["formal_reply_sent"] is False


def test_diplomacy_detects_conditional_two_day_hormuz_plan():
    module, config = _load("diplomacy-monitor", "diplomacy_monitor.py")
    result = module.Engine(config).classify(
        "Araghchi says Qatar-mediated two-day plan could reopen Strait of Hormuz",
        "The United States would implement Iran's conditions on day one and normal navigation would resume on day two.",
    )
    assert result is not None
    assert "proposal_exchange" in result["buckets"]
    assert result["flags"]["conditional_reopening_plan"] is True
    assert result["flags"]["formal_reply_sent"] is False


def test_diplomacy_rejects_unrelated_person_and_letter_mentions():
    module, config = _load("diplomacy-monitor", "diplomacy_monitor.py")
    engine = module.Engine(config)
    unrelated = engine.classify(
        "Vance writes a campaign letter",
        "The letter discusses domestic election fundraising without Iran or negotiations.",
    )
    assert unrelated is None or "proposal_exchange" not in (unrelated.get("buckets") or [])


def test_readiness_detects_presidential_strike_options_briefing():
    module, config = _load("readiness-monitor", "readiness_monitor.py")
    module.SIG_CFG = config["signals"]
    signals = module.signals_of(
        "Senior US military officials briefed the president on strike options against Iran.",
    )
    assert any(item["bucket"] == "strike_options_briefing" for item in signals)


def test_israel_detects_airlifted_air_defense_resupply_only_in_conflict_context():
    module, config = _load("israel-monitor", "israel_monitor.py")
    engine = module.Engine(config)
    result = engine.classify(
        "Ten US transport aircraft arrived at an Israeli base",
        "The airlift delivered air defense interceptors as tensions with Iran rose.",
    )
    assert result is not None
    assert "airlift_resupply" in result["buckets"]
    unrelated = engine.classify(
        "Transport aircraft arrived for a civil aviation exhibition",
        "The event displayed passenger aircraft without Israel, Iran or military resupply.",
    )
    assert unrelated is None or "airlift_resupply" not in (unrelated.get("buckets") or [])


def test_adapter_preserves_new_buckets_flags_and_signals():
    from backend.adapters import normalize

    diplomacy = normalize({
        "title": "Rezaei reviewed Vance letter but has not responded",
        "url": "https://example.org/diplomacy",
        "buckets": ["proposal_exchange"],
        "flags": {"letter_received": True, "formal_reply_pending": True},
    }, "diplomacy-monitor")
    assert diplomacy["topics"] == ["usiran"]
    assert diplomacy["dimensions"]["usiran"] == "diplomacy"
    values = diplomacy["upstream_extractions"][0]["values"]
    assert values["buckets"] == ["proposal_exchange"]
    assert values["flags"]["formal_reply_pending"] is True

    readiness = normalize({
        "title": "US officials briefed the president on strike options against Iran",
        "url": "https://example.org/readiness",
        "signals": [{"bucket": "strike_options_briefing"}],
        "party": "us",
    }, "readiness-monitor")
    assert readiness["topics"] == ["usiran"]
    assert readiness["upstream_extractions"][0]["values"]["signals"][0]["bucket"] == "strike_options_briefing"
