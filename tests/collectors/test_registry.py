from __future__ import annotations

import json
from pathlib import Path

from scripts.collect import select_collectors
from scripts.integrate_collectors import load_registry


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "collectors" / "collectors.json"
KNOWN_OUTPUT_KINDS = {
    "records-jsonl",
    "records-and-metrics",
    "finance-series",
    "geo-v6",
    "taco",
}


def test_registry_is_well_formed_and_complete():
    registry = load_registry(REGISTRY_PATH)
    assert len(registry) == 19
    ids = [item["id"] for item in registry]
    assert len(ids) == len(set(ids))

    for item in registry:
        assert {"id", "name_zh", "topics", "entrypoint", "selftest_args", "output_kind", "enabled"} <= item.keys()
        assert item["id"]
        assert item["topics"]
        assert isinstance(item["enabled"], bool)
        assert isinstance(item["selftest_args"], list)
        assert item["output_kind"] in KNOWN_OUTPUT_KINDS

        entrypoint = (ROOT / "collectors" / item["id"] / item["entrypoint"]).resolve()
        assert entrypoint.is_file(), f"missing entrypoint for {item['id']}: {entrypoint}"
        collector_dir = ROOT / "collectors" / item["id"]
        assert (collector_dir / "README.md").is_file(), f"missing README for {item['id']}"


def test_registry_entrypoints_stay_inside_repository_and_compile():
    registry = load_registry(REGISTRY_PATH)
    for item in registry:
        entrypoint = (ROOT / "collectors" / item["id"] / item["entrypoint"]).resolve()
        assert ROOT in entrypoint.parents
        compile(entrypoint.read_text(encoding="utf-8"), str(entrypoint), "exec")


def test_runner_can_select_every_registered_collector():
    registry = load_registry(REGISTRY_PATH)
    selected = select_collectors(registry, [], None)
    assert [item["id"] for item in selected] == [item["id"] for item in registry]


def test_registry_json_matches_loader():
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert raw["collectors"] == load_registry(REGISTRY_PATH)
