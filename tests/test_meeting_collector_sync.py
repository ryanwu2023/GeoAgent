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


def test_range_signal_refines_dated_event_without_type_error():
    """2026-11-22 起：区间日期信号更新单日事件时 str 与 date 混比，采集器整轮崩溃。"""
    monitor = load_monitor()
    ev = _event(monitor, "EV-1", ["modi", "trump"], "2026-11-17", "2026-11-17", "day")
    sig = {"status": "arranged", "rank": 65, "tier": 3, "source_name": "Google News", "source_id": "q",
           "outlet": "x", "link": "https://example.test/r", "persons": ["modi", "trump"], "actors": [],
           "location": "马尼拉", "location_raw": "Manila", "published": "2026-11-15", "quote": "talks Nov 16-18",
           "date_start": "2026-11-16", "date_end": "2026-11-18", "precision": "range", "date_raw": "Nov 16-18",
           "family": "bilateral", "title": "Modi Trump talks Nov 16-18 in Manila"}
    assert monitor.upsert(sig, [ev], [2], []) is ev
    assert (ev["date_start"], ev["date_end"], ev["precision"]) == ("2026-11-16", "2026-11-18", "range")


def test_same_day_duplicates_merge_when_one_side_names_more_people():
    """同一场四方会谈：一条写莫迪+特朗普，一条还写了高市早苗，都在 11-17，应合并成旧编号那条。"""
    monitor = load_monitor()
    older = _event(monitor, "EV-0036", ["modi", "trump"], "2026-11-17", "2026-11-17", "day",
                   claims=[_claim("held", 95, "Trump meets Modi in Manila", "2026-11-17")])
    newer = _event(monitor, "EV-0065", ["modi", "trump", "takaichi"], "2026-11-17", "2026-11-17", "day",
                   claims=[_claim("held", 95, "Trump meets Quad leaders Modi, Takaichi", "2026-11-17")])
    events, transitions = [older, newer], []
    assert monitor.consolidate(events, transitions) == 1
    assert events == [older] and transitions[0]["id"] == "EV-0065"


def test_meeting_noun_phrase_about_a_future_or_possible_meeting_is_not_held():
    monitor = load_monitor()
    prospective = {
        "China discussing possible meeting between Xi, Putin, Trump, says Beijing": "proposed",
        "Trump hints at upcoming meeting with Putin, Zelensky amid Ukraine peace push": "proposed",
        "Trump says his potential meeting with Putin, Xi in China would be interesting": "proposed",
        "Trump talks about the meeting between Putin and Zelensky": "proposed",
        "Xi Jinping to hold trilateral meeting with Putin, Trump on November 19": "arranged",
        "Trump arrives in China for historic meeting with Xi, Putin": "arranged",
        "Putin says EU is losing industry, ahead of meeting with Trump and Xi": "arranged",
    }
    for headline, want in prospective.items():
        assert monitor.detect_status(headline)[0] == want, headline
    for headline in ("Trump touts meeting with Xi and Putin but offers few results",
                     "Trump to hold press conference after meeting with Xi and Putin",
                     "Masoud Pezeshkian Meets Qatar Emir Tamim Al Thani in Moscow"):
        assert monitor.detect_status(headline)[0] == "held", headline


def test_held_needs_two_people_who_actually_met():
    monitor = load_monitor()
    assert monitor.held_participants("Trump met with Putin’s envoy at the White House") == {"trump"}
    assert monitor.held_participants("Russia 'lifts ban on killing Zelensky' after high-stakes meeting with Putin") == {"putin"}
    assert monitor.held_participants("Trump called Zelensky after meeting with Putin") == {"trump", "putin"}
    assert monitor.held_participants("During Meeting with Pezeshkian, Putin Expects 'Positive Developments'") == {"pezeshkian", "putin"}

    item = {"title": "Zelensky meets with Trump's envoys in Dublin after their summit with Putin",
            "link": "https://x.example/1", "source_id": "q", "source_name": "q", "tier": 3,
            "published": monitor.TODAY, "desc": ""}
    signals, rejects = monitor.build_signals([item], monitor.load_cfg())
    assert all(s["status"] != "held" for s in signals)


def test_speaker_city_is_not_the_meeting_location():
    monitor = load_monitor()
    loc = lambda text: monitor.extract_location(text, monitor.LOCS)[0]
    assert loc("Putin, Xi and Trump to meet in Shenzhen on November 19, Kremlin says") == "深圳"
    assert loc("Moscow confirms Trump, Xi, Putin meeting in China's Shenzhen") == "深圳"
    assert loc("China discussing possible meeting between Xi, Putin, Trump, says Beijing") == ""
    assert loc("Putin met with Pezeshkian in Moscow") == "莫斯科"


def test_stored_false_held_claims_are_rechecked_and_event_lapses():
    monitor = load_monitor()
    day = (monitor.TODAY - timedelta(days=10)).isoformat()
    claim = lambda quote, status="held", rank=95: {
        "status": status, "rank": rank, "tier": 3, "source": "q", "source_id": "q", "outlet": "",
        "url": "u" + quote[:8], "persons": ["putin", "zelensky"], "location": "", "published": day,
        "quote": quote, "date_start": day, "precision": "day", "anchor_snap": ""}
    ev = {"id": "EV-0001", "family": "bilateral", "title": "", "title_raw": "x", "date_start": day,
          "date_end": day, "precision": "day", "date_raw": "", "location": "", "location_raw": "",
          "persons": ["putin", "zelensky"], "actors": [], "track": "ru-ua", "status": "held",
          "conflict": False, "claims": [
              claim("Trump says a meeting between Putin and Zelensky to end Ukraine war will happen"),
              claim("Russia 'lifts ban on killing Zelensky' after high-stakes meeting with Putin"),
              claim("Zelensky says he's ready to meet Putin in Turkey", "arranged", 65)],
          "sources": [], "first_seen": day, "last_seen": day, "updates": [], "baseline": False,
          "baseline_ids": [], "days_seen": 1}
    transitions = []
    assert monitor.recheck_claims([ev], transitions) == 1
    monitor.update_lifecycle(ev, monitor.TODAY, transitions)
    assert ev["status"] == "lapsed"
    assert all(c["status"] != "held" for c in ev["claims"])


def test_truncated_evidence_quote_is_rebuilt_before_rechecking():
    monitor = load_monitor()
    quote = ("urges immediate Iran-US peace talks in meeting with Pezeshkian at COP31. "
             "Erdogan urges immediate Iran")
    rebuilt = monitor.rebuild_headline(quote)
    assert rebuilt.startswith("Erdogan urges immediate Iran-US peace talks")
    assert monitor.held_participants(rebuilt) == {"erdogan", "pezeshkian"}
    assert monitor.held_participants("Wes Streeting calls Putin a killer as he meets Zelensky in Kyiv") == {"zelensky"}


def test_held_report_attaches_only_to_event_with_both_principals():
    monitor = load_monitor()
    day = (monitor.TODAY - timedelta(days=5)).isoformat()
    def event(eid, persons, claims_persons, family="bilateral", date=day, precision="day"):
        return {"id": eid, "family": family, "persons": persons, "actors": [], "location": "",
                "date_start": date, "date_end": date, "precision": precision,
                "claims": [{"persons": p} for p in claims_persons]}
    merz = event("EV-0001", ["merz", "putin", "trump"], [["merz", "putin"], ["merz", "putin", "trump"]])
    summit = event("EV-0002", ["putin", "trump", "xi"], [["putin", "trump", "xi"]] * 3, family="trilateral",
                   date=(monitor.TODAY - timedelta(days=6)).isoformat())
    undated = event("EV-0003", ["putin", "trump"], [["putin", "trump"]], date="", precision="tbd")
    sig = {"family": "bilateral", "persons": ["putin", "trump"], "status": "held", "date_approx": True,
           "date_start": day, "date_end": day, "precision": "day", "location": ""}
    assert monitor.find_match(sig, [merz, summit, undated])["id"] == "EV-0002"


def test_held_location_comes_from_the_meeting_clause():
    monitor = load_monitor()
    item = {"title": "Trump meets Putin and Xi in China, while European imperialists plot escalation at Berlin summit",
            "link": "https://x.example/2", "source_id": "q", "source_name": "q", "tier": 3,
            "published": monitor.TODAY, "desc": ""}
    [sig], _ = monitor.build_signals([item], monitor.load_cfg())
    assert sig["status"] == "held" and sig["location"] == ""
    item["title"] = "Trump called Zelensky after meeting with Putin in Shenzhen"
    [sig], _ = monitor.build_signals([item], monitor.load_cfg())
    assert sig["persons"] == ["putin", "trump"] and sig["location"] == "深圳"


def test_unnamed_meeting_partner_does_not_pull_in_every_name_in_the_headline():
    monitor = load_monitor()
    headline = ("'For us he is a killer': Zelensky rebukes Trump for calling Putin his 'friend' - as it's claimed "
                "Russian leader increases strikes on Ukraine after every meeting with President")
    assert len(monitor.held_participants(headline)) < 2
    assert monitor.held_participants("Putin and Pezeshkian held talks at the Kremlin") == {"putin", "pezeshkian"}
    assert monitor.held_participants("Erdoğan wants to discuss ending the war with Putin after meeting with Zelenskyy") \
        == {"erdogan", "zelensky"}
