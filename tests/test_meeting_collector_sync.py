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


def _event(monitor, eid, persons, start, end, precision, status="arranged", claims=None):
    ev = {"id": eid, "family": "bilateral", "title": "", "title_raw": "", "date_start": start, "date_end": end,
          "precision": precision, "date_raw": "", "location": "", "location_raw": "", "persons": persons,
          "actors": [], "track": "asia", "status": status, "conflict": False, "claims": claims or [], "sources": [],
          "first_seen": "2026-10-31 10:00:00", "last_seen": "", "updates": [], "baseline": False,
          "baseline_ids": [], "days_seen": 1}
    return ev


def _claim(status, rank, quote, published):
    return {"status": status, "rank": rank, "tier": 3, "source": "Google News", "source_id": "q", "outlet": "x",
            "url": "https://example.test/" + quote[:8], "persons": ["modi", "trump"], "location": "",
            "published": published, "quote": quote, "date_start": "", "precision": "tbd", "anchor_snap": ""}


def test_month_window_event_merges_into_dated_meeting_with_location_and_held():
    """2026-11-23：「11 月在马尼拉见」与「11 月 17 日在马尼拉见」被拆成两条，前者一直显示 11 月内、地点待定。"""
    from datetime import date
    monitor = load_monitor()
    monitor.TODAY = date(2026, 11, 23)
    month = _event(monitor, "EV-1", ["modi", "trump"], "2026-11-01", "2026-11-30", "month",
                   claims=[_claim("arranged", 65, "PM Modi, Trump to meet in Manila next month?", "2026-10-17")])
    day = _event(monitor, "EV-2", ["modi", "trump", "takaichi"], "2026-11-17", "2026-11-17", "day",
                 claims=[_claim("arranged", 65, "PM Modi, Trump to meet in Manila on November 17", "2026-11-14")])
    events, transitions = [month, day], []
    assert monitor.consolidate(events, transitions) == 1
    assert events == [day] and day["location"] == "马尼拉"
    assert [q["q"] for q in monitor.verify_queries(events, monitor.TODAY)] == [
        "Modi Trump (met OR meets OR talks OR meeting) when:7d"]
    status, _, rank = monitor.detect_status("Read Modi's 'magic' in Manila: PM Modi meets Donald Trump")
    assert status == "held"
    day["claims"].append({**_claim(status, rank, "PM Modi meets Donald Trump", "2026-11-18"), "location": "马尼拉"})
    day["status"], _ = monitor._recompute(day)
    monitor.update_lifecycle(day, monitor.TODAY, transitions)
    assert day["status"] == "held"


def test_month_window_needs_named_people_on_both_sides():
    monitor = load_monitor()
    month = _event(monitor, "EV-1", ["putin", "zelensky"], "2026-11-01", "2026-11-30", "month")
    other = _event(monitor, "EV-2", [], "2026-11-10", "2026-11-11", "range")
    assert monitor.consolidate([month, other], []) == 0
    assert monitor.detect_status("Modi to meet Trump in Manila")[0] == "arranged"
    assert monitor.extract_location("Quad leaders gather in Manila", monitor.LOCS)[0] == "马尼拉"
