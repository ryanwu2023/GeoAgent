from backend.qa_readiness import evaluate_readiness, select_faceted_reads


def _entry(facet, rid, *, status="reported", full=True, conflicting=False):
    return {
        "ledger_id": f"l-{rid}",
        "facet": facet,
        "claim": f"claim-{rid}",
        "status": status,
        "supporting_ids": [rid],
        "conflicting_ids": [f"c-{rid}"] if conflicting else [],
        "full_text_ids": [rid] if full else [],
        "published_at": "2026-10-25T01:00:00Z",
    }


def test_readiness_rejects_early_final_when_required_facets_are_missing():
    plan = {
        "question": "最新美伊局势分析",
        "focuses": ["事件主线", "外交", "军事", "反证与不确定性"],
        "needs_metrics": False,
    }
    report = evaluate_readiness(
        plan,
        {"entries": [_entry("event", "e1"), _entry("diplomacy", "d1")]},
        {
            "searched_facets": ["event", "diplomacy"],
            "checked_windows": ["48h", "7d"],
            "counter_search_done": False,
            "answer_as_of": "2026-10-25",
        },
        {"used": 5, "maximum": 20},
    )
    assert report.ready is False
    assert "military" in report.missing_facets
    assert "counterevidence" in report.missing_facets
    assert any("反证" in item for item in report.follow_up_tasks)


def test_readiness_accepts_conflict_and_confirmed_gap_as_terminal_coverage():
    plan = {
        "question": "最新俄乌情况",
        "focuses": ["事件主线", "军事", "反证与不确定性"],
        "needs_metrics": False,
    }
    report = evaluate_readiness(
        plan,
        {"entries": [
            _entry("event", "e1"),
            _entry("military", "m1", conflicting=True),
            _entry("counterevidence", "g1", status="gap", full=False),
        ]},
        {
            "searched_facets": ["event", "military", "counterevidence"],
            "checked_windows": ["48h", "7d"],
            "counter_search_done": True,
            "answer_as_of": "2026-10-25",
            "latest_high_confidence_at": "2026-10-25T01:00:00Z",
        },
        {"used": 10, "maximum": 20},
    )
    assert report.ready is True
    assert report.coverage["military"].status == "conflicted"
    assert report.coverage["counterevidence"].status == "gap_confirmed"


def test_readiness_forces_degraded_synthesis_when_budget_is_exhausted():
    report = evaluate_readiness(
        {"question": "最新美伊局势", "focuses": ["事件主线", "军事"]},
        {"entries": [_entry("event", "e1")]},
        {"searched_facets": ["event"], "checked_windows": ["48h"]},
        {"used": 8, "maximum": 8},
    )
    assert report.ready is True
    assert report.forced is True
    assert report.coverage["military"].status == "budget_exhausted"


def test_readiness_emits_stale_warning_for_latest_question():
    report = evaluate_readiness(
        {"question": "最新美伊局势", "focuses": ["事件主线"]},
        {"entries": [_entry("event", "e1")]},
        {
            "searched_facets": ["event"],
            "checked_windows": ["48h", "7d"],
            "counter_search_done": True,
            "answer_as_of": "2026-10-25",
            "latest_high_confidence_at": "2026-10-20T01:00:00Z",
        },
        {"used": 2, "maximum": 8},
    )
    assert report.ready is True
    assert "72 小时" in report.stale_warning


def test_market_focus_requires_metric_series():
    plan = {"question": "当前资产影响", "focuses": ["市场与资产"], "needs_metrics": True}
    missing = evaluate_readiness(
        plan, {"entries": [_entry("market", "e1")]},
        {"searched_facets": ["market"], "metric_series": []},
        {"used": 2, "maximum": 8},
    )
    assert missing.ready is False
    assert any("指标" in item for item in missing.follow_up_tasks)
    ready = evaluate_readiness(
        plan, {"entries": [_entry("market", "e1")]},
        {
            "searched_facets": ["market"],
            "metric_series": ["finance:brent"],
            "checked_windows": ["48h", "7d"],
        },
        {"used": 3, "maximum": 8},
    )
    assert ready.ready is True


def test_faceted_reads_reserve_space_for_each_required_facet():
    records = [
        {"id": "e1", "facet": "event", "published_at": "2026-10-25", "source_name": "A"},
        {"id": "e2", "facet": "event", "published_at": "2026-10-24", "source_name": "A"},
        {"id": "e3", "facet": "event", "published_at": "2026-10-23", "source_name": "A"},
        {"id": "d1", "facet": "diplomacy", "published_at": "2026-10-24", "source_name": "B"},
        {"id": "m1", "facet": "military", "published_at": "2026-10-24", "source_name": "C"},
        {"id": "c1", "facet": "counterevidence", "published_at": "2026-10-22", "source_name": "D"},
    ]
    selected = select_faceted_reads(records, ["event", "diplomacy", "military", "counterevidence"], limit=4)
    assert {item["facet"] for item in selected} == {"event", "diplomacy", "military", "counterevidence"}
