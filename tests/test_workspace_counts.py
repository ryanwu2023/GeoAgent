"""Lightweight workspaces retain full-scope counts, not map/page counts."""
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from backend.adapters import normalize
from backend.app import app
from backend.geo_mcp.server import _workspace_counts
from backend.store import db, get_snapshot, save_snapshot


@pytest.fixture
def scoped_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    get_snapshot.cache_clear()

    def record(name, signal, *, topic="usiran", dimension="diplomacy",
               published="2026-10-01", supplemental=False, mapped=False):
        item = normalize({
            "title": "Scoped bulletin",
            "summary": "A source statement requiring review.",
            "link": f"https://example.org/{name}",
        }, "test-monitor")
        item.update(
            topics=[topic], dimensions={topic: dimension},
            dimension_candidates={topic: [dimension]}, published_at=published,
            situation_signal=signal, signal_basis="fixture",
            supplemental=supplemental,
            points=[{"label": "Example", "lon": 10, "lat": 20,
                     "precision": "explicit", "role": "mention"}] if mapped else [],
        )
        return item

    records = [
        record("reviewed", "escalation", mapped=True),
        record("no-location", "sustain"),
        record("supplemental", "escalation", supplemental=True),
        record("other-dimension", "escalation", dimension="financial"),
        record("earlier", "easing", published="2026-09-30"),
        record("other-topic", "escalation", topic="ukraine"),
    ]
    sid, _ = save_snapshot({
        "records": records, "observations": [], "sources": [],
        "manifest": [], "errors": [],
    })
    with db() as connection:
        for version, signal in [(1, "sustain"), (2, "easing")]:
            connection.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
                               (sid, records[0]["id"], version, signal,
                                "reviewer", "Reviewed statement", "2026-10-02"))
        # A review in another snapshot must not alter this snapshot's totals.
        connection.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
                           ("other-snapshot", records[1]["id"], 1, "escalation",
                            "reviewer", "Unrelated review", "2026-10-02"))
    yield sid
    get_snapshot.cache_clear()


@pytest.mark.parametrize("filters", [
    {"topic": "all"},
    {"topic": "usiran"},
    {"topic": "ukraine"},
    {"topic": "usiran", "dimension": "diplomacy",
     "start": "2026-10-01", "end": "2026-10-01"},
    {"topic": "usiran", "start": "2026-10-05"},
])
def test_light_and_full_workspace_counts_match_reviewed_records_and_mcp(scoped_snapshot, filters):
    client = TestClient(app)
    params = {"snapshot_id": scoped_snapshot, **filters}
    full = client.get("/api/workspace", params=params).json()
    light = client.get("/api/workspace", params={**params, "include_records": False}).json()
    mcp = _workspace_counts(get_snapshot(scoped_snapshot),
                            filters.get("topic", "all"), filters.get("dimension", ""),
                            filters.get("start", ""), filters.get("end", ""))

    assert light["records"] == []
    assert light["records_count"] == len(full["records"])
    expected_signals = Counter(item["situation_signal"] for item in full["records"])
    for signal, count in light["signal_counts"].items():
        assert count == expected_signals[signal]
    assert sum(light["signal_counts"].values()) == light["records_count"]
    for field in ("records_count", "signal_counts", "event_category_counts"):
        assert light[field] == full[field] == mcp[field]


def test_scope_counts_include_unmapped_and_supplemental_not_just_map_payload(scoped_snapshot):
    client = TestClient(app)
    params = {"snapshot_id": scoped_snapshot, "topic": "usiran",
              "dimension": "diplomacy", "start": "2026-10-01", "end": "2026-10-01"}
    light = client.get("/api/workspace", params={**params, "include_records": False}).json()
    mapped = client.get("/api/map-records", params=params).json()

    assert light["records_count"] == 3
    assert light["signal_counts"] == {"escalation": 0, "sustain": 1, "easing": 1, "unknown": 1}
    assert len(mapped) == 1
    assert mapped[0]["situation_signal"] == "easing"
