import math
from pathlib import Path

from backend.taco_adapter import load_taco


FIXTURE = Path(__file__).parent / "fixtures" / "taco"


def test_taco_adapter_keeps_hormuz_as_observation_only():
    records, observations, sources = {}, [], []
    load_taco(FIXTURE, records, observations, sources)
    taco = next(x for x in observations if x["id"] == "taco:TACO_INDEX")
    hormuz = next(x for x in observations if x["id"] == "taco:HORMUZ_TRANSIT")
    assert taco["method"].startswith("5 因子")
    assert "不进入 TACO" in hormuz["boundary"]
    assert hormuz["history"][-1]["date"] == "2026-09-20"


def test_taco_adapter_discards_non_finite_values():
    records, observations, sources = {}, [], []
    load_taco(FIXTURE, records, observations, sources)
    assert all(
        math.isfinite(point["value"])
        for item in observations
        for point in item["history"]
        if point["value"] is not None
    )


def test_taco_methodology_record_links_frozen_raw_file():
    records, observations, sources, manifests = {}, [], [], []

    def read(path):
        manifests.append({"archive": "frozen-taco.csv"})
        return path.read_text(encoding="utf-8-sig")

    load_taco(FIXTURE, records, observations, sources, read, manifests)
    assert records["taco-methodology"]["raw_refs"] == ["frozen-taco.csv"]
    assert records["taco-methodology"]["url"] == "/api/archive/frozen-taco.csv"
