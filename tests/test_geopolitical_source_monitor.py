from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collectors" / "geopolitical-source-monitor"


def load_module(name: str):
    path = COLLECTOR / f"{name}.py"
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

