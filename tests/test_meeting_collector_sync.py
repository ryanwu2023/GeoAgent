import importlib.util
import json
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collectors" / "meeting-calendar-monitor"


def load_monitor():
    spec = importlib.util.spec_from_file_location("meeting_monitor_sync", COLLECTOR / "meeting_monitor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.bind_globals(module.load_cfg())
    return module


def test_latest_calendar_config_expands_tracks_people_queries_and_anchors():
    config = json.loads((COLLECTOR / "config.json").read_text(encoding="utf-8"))

    assert {"cn-eu", "asia", "mideast"}.issubset(config["tracks"])
    person_ids = {item["id"] for item in config["persons"]}
    assert {"steinmeier", "takaichi", "lee_jm", "mbs", "erdogan"}.issubset(person_ids)
    query_ids = {item["id"] for item in config["queries"]}
    assert {"q_cneu_1", "q_asia_1", "q_me_1"}.issubset(query_ids)
    anchor_ids = {item["id"] for item in config["anchors"]}
    assert {"a_steinmeier_cn", "a_merz_jp", "a_merz_kr"}.issubset(anchor_ids)
    assert next(item for item in config["sources"] if item["id"] == "state")["egress"] == "direct"


def test_not_attending_is_denied_and_not_rendered_as_date_pending():
    monitor = load_monitor()

    status, phrase, rank = monitor.detect_status("Ghalibaf will not attend BRICS parliamentary summit")

    assert status == "denied"
    assert phrase == "not-attend"
    assert rank < 0
    assert monitor.status_cell({"status": "denied", "date_start": ""}) == "已否认"


def test_status_conflict_accepts_persisted_iso_dates_without_type_error():
    monitor = load_monitor()
    claims = [
        {"status": "denied", "rank": -4, "tier": 1, "published": "2026-10-20"},
        {"status": "confirmed", "rank": 4, "tier": 1, "published": "2026-10-23"},
    ]

    assert monitor.choose_status(claims) == ("confirmed", True)
    assert monitor._pub_date("2026-10-23") == monitor.TODAY.replace(year=2026, month=10, day=23)
    assert monitor._pub_date("2026-10-20") + timedelta(days=3) == monitor._pub_date("2026-10-23")


def test_collector_keeps_runtime_output_and_state_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLECTOR_OUTPUT_ROOT", str(tmp_path / "published"))
    monkeypatch.setenv("COLLECTOR_STATE_ROOT", str(tmp_path / "state"))

    monitor = load_monitor()

    assert Path(monitor.OUTPUT) == tmp_path / "published"
    assert Path(monitor.STATE) == tmp_path / "state"
