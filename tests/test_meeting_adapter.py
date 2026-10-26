import json

from backend.adapters import load_bundle
from backend.chinese import present_map_record


def write_collector(root, rows):
    project = root / "meeting-calendar-monitor"
    output = project / "output"
    output.mkdir(parents=True)
    (output / "LATEST.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    (project / "config.json").write_text(json.dumps({"persons": [
        {"id": "putin", "zh": "普京", "role": "俄罗斯总统", "country": "俄罗斯"},
        {"id": "pezeshkian", "zh": "佩泽希齐扬", "role": "伊朗总统", "country": "伊朗"},
    ]}, ensure_ascii=False), encoding="utf-8")


def test_meeting_calendar_preserves_status_precision_sources_and_map_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "collectors"
    write_collector(root, [{
        "id": "EV-1", "title": "俄伊领导人双边会晤", "family": "bilateral",
        "date_start": "2026-11-01", "date_end": "", "precision": "month",
        "location": "莫斯科", "persons": ["putin", "pezeshkian"],
        "actors": ["russia", "iran"], "track": "multi", "status": "arranged",
        "conflict": True, "first_seen": "2026-10-24 08:00:00", "last_seen": "2026-10-25 08:00:00",
        "claims": [{"status": "arranged", "tier": 2, "source": "IRNA", "url": "https://example.com/a"},
                   {"status": "denied", "tier": 3, "source": "Media", "url": "https://example.com/b"}],
        "sources": [{"name": "IRNA", "tier": 2, "url": "https://example.com/a", "published": "2026-10-24"}],
        "updates": [{"from": "proposed", "to": "arranged"}],
    }, {
        "id": "EV-2", "title": "美国中期选举（非会晤）", "family": "election",
        "date_start": "2026-11-03", "precision": "day", "location": "美国",
        "persons": [], "actors": ["usa"], "track": "multi", "status": "confirmed",
        "sources": [], "claims": [], "updates": [], "baseline": True,
    }])

    bundle = load_bundle(root)

    assert len(bundle["meetings"]) == 2
    meeting = next(item for item in bundle["meetings"] if item["id"] == "EV-1")
    assert meeting["precision"] == "month"
    assert meeting["date_start"] == "2026-11-01"
    assert meeting["status_label"] == "已安排"
    assert meeting["conflict"] is True
    assert {person["name"] for person in meeting["persons"]} == {"普京", "佩泽希齐扬"}
    assert {person["role"] for person in meeting["persons"]} == {"俄罗斯总统", "伊朗总统"}
    assert meeting["topics"] == ["usiran", "ukraine"]
    assert meeting["points"][0]["role"] == "计划会晤地点，不代表实时位置"
    record = next(item for item in bundle["records"] if item["id"] == meeting["evidence_id"])
    assert record["dimensions"] == {"usiran": "diplomacy", "ukraine": "diplomacy"}
    assert record["event_at"] == "2026-11-01"
    assert "普京（俄罗斯总统）" in record["text"]
    assert present_map_record(record, {})["points"] == []
    non_meeting = next(item for item in bundle["meetings"] if item["id"] == "EV-2")
    assert non_meeting["non_meeting"] is True
    assert non_meeting["points"] == []


def test_inactive_and_unknown_location_are_not_mapped(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "collectors"
    write_collector(root, [{
        "id": "EV-3", "title": "潜在谈判", "family": "bilateral", "date_start": "",
        "precision": "tbd", "location": "地点未定", "persons": ["pezeshkian"],
        "actors": ["iran", "usa"], "track": "us-ir", "status": "lapsed",
        "sources": [], "claims": [], "updates": [],
    }])
    meeting = load_bundle(root)["meetings"][0]
    assert meeting["inactive"] is True
    assert meeting["points"] == []
    assert meeting["date_start"] == ""


def test_people_config_falls_back_to_packaged_collector_source(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    runtime_root = tmp_path / "runtime"
    source_root = tmp_path / "source"
    write_collector(source_root, [])
    runtime_project = runtime_root / "meeting-calendar-monitor" / "output"
    runtime_project.mkdir(parents=True)
    runtime_project.joinpath("LATEST.jsonl").write_text(json.dumps({
        "id": "EV-4", "title": "俄伊领导人会晤", "family": "bilateral",
        "date_start": "2026-11-10", "precision": "day", "location": "莫斯科",
        "persons": ["putin", "pezeshkian"], "actors": ["russia", "iran"],
        "track": "multi", "status": "scheduled", "sources": [], "claims": [], "updates": [],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr("backend.paths.collector_source_root", lambda: source_root)

    meeting = load_bundle(runtime_root)["meetings"][0]

    assert [(person["name"], person["role"]) for person in meeting["persons"]] == [
        ("普京", "俄罗斯总统"), ("佩泽希齐扬", "伊朗总统"),
    ]


def test_expanded_calendar_tracks_keep_chinese_labels_and_mappable_venues(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "collectors"
    project = root / "meeting-calendar-monitor"
    output = project / "output"
    output.mkdir(parents=True)
    (project / "config.json").write_text(json.dumps({"persons": [
        {"id": "merz", "zh": "默茨", "role": "德国总理", "country": "德国"},
        {"id": "lee_jm", "zh": "李在明", "role": "韩国总统", "country": "韩国"},
    ]}, ensure_ascii=False), encoding="utf-8")
    (output / "LATEST.jsonl").write_text(json.dumps({
        "id": "EV-ASIA", "title": "默茨—李在明首脑会谈", "family": "bilateral",
        "date_start": "2026-10-29", "precision": "day", "location": "首尔",
        "persons": ["merz", "lee_jm"], "actors": ["germany", "south_korea"],
        "track": "asia", "status": "arranged", "sources": [], "claims": [], "updates": [],
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    meeting = load_bundle(root)["meetings"][0]

    assert meeting["track_label"] == "亚洲与印太"
    assert [person["name"] for person in meeting["persons"]] == ["默茨", "李在明"]
    assert meeting["points"][0]["label"] == "首尔"
    assert meeting["points"][0]["role"] == "计划会晤地点，不代表实时位置"
