import json

from backend.qa_context import ContextManager, ToolEnvelope, build_preview, estimate_tokens
from backend.qa_harness_state import HarnessBudget, HarnessScope
from backend.qa_harness_store import HarnessStore


def test_preview_preserves_valid_structure_for_large_multibyte_result():
    payload = {"records": [{"id": f"r{i}", "title": "中文标题", "text": "多行\n正文" * 500} for i in range(20)], "total": 20}
    preview = build_preview(payload, 220)
    assert isinstance(preview, dict)
    assert json.loads(json.dumps(preview, ensure_ascii=False)) == preview
    assert estimate_tokens(payload) > estimate_tokens(preview)


def test_text_preview_honors_cjk_token_budget():
    preview = build_preview("中文" * 1000, 80)
    assert isinstance(preview, str)
    assert preview.endswith("…[预览已截断]")
    assert estimate_tokens(preview) <= 80


def test_context_stores_full_artifact_and_keeps_stable_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    scope = HarnessScope("s1")
    store.create_run(run_id="run", answer_id="a", scope=scope, question="q", plan={}, budget=HarnessBudget(), model="m")
    context = ContextManager("run", store, "问题", "系统", {}, soft_tokens=10000)
    payload = {"records": [{"id": "r1", "title": "标题", "text": "正文" * 5000}]}
    first = context.add_tool_result(ToolEnvelope("c1", "geo_evidence_search", True, None), payload)
    original_preview = first.preview
    second = context.add_tool_result(ToolEnvelope("c1", "geo_evidence_search", True, None), {"changed": True})
    assert second.preview == original_preview
    assert store.load_artifact(second.artifact_id) == {"changed": True}


def test_context_replaces_failed_preview_after_same_call_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    scope = HarnessScope("s1")
    store.create_run(run_id="retry", answer_id="a", scope=scope, question="q", plan={}, budget=HarnessBudget(), model="m")
    context = ContextManager("retry", store, "问题", "系统", {}, soft_tokens=10000)

    failed = context.add_tool_result(
        ToolEnvelope("same-call", "geo_evidence_search", False, None),
        {"error": {"kind": "tool_unavailable", "message": "暂时失败"}},
    )
    assert "error" in failed.preview

    succeeded = context.add_tool_result(
        ToolEnvelope("same-call", "geo_evidence_search", True, None),
        {"records": [{"id": "r1", "title": "恢复后的证据"}]},
    )
    assert succeeded.preview["records"][0]["id"] == "r1"
    assert context.memory["evidence_ids"] == ["r1"]


def test_context_keeps_complete_compact_metrics_and_scope_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    store.create_run(run_id="facts", answer_id="a", scope=HarnessScope("s1"), question="q", plan={}, budget=HarnessBudget(), model="m")
    context = ContextManager("facts", store, "问题", "系统", {}, preview_tokens=80)
    context.add_tool_result(
        ToolEnvelope("scope", "geo_workspace_summary", True, None),
        {"records_count": 1873, "source_count": 236, "signal_counts": {"escalation": 108, "easing": 9}, "metrics_count": 37},
    )
    metrics = [{"series": f"s{i}", "name": f"指标{i}", "current": {"date": "2026-10-24", "value": i}, "published_at": "ignored", "source_ids": [f"r{i}"]} for i in range(12)]
    context.add_tool_result(ToolEnvelope("metrics", "geo_metric_catalog", True, None), {"metrics": metrics, "total": 12})
    assert context.memory["scope_summary"]["records_count"] == 1873
    assert len(context.memory["metrics"]) == 12
    assert "published_at" not in context.memory["metrics"][0]
    assert context.memory["metrics"][0]["source_ids"] == ["r0"]
    assert context.memory["metric_availability"][0] == {
        "series": "s0", "name": "指标0", "date": "2026-10-24", "value": 0,
    }


def test_context_builds_bounded_metric_block_from_series_receipts(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    store.create_run(
        run_id="metric-block", answer_id="a", scope=HarnessScope("s1"),
        question="q", plan={}, budget=HarnessBudget(), model="m",
    )
    context = ContextManager("metric-block", store, "问题", "系统", {})
    context.add_tool_result(
        ToolEnvelope("brent", "geo_metric_series", True, None),
        {
            "metric": {
                "series": "finance:brent", "name": "布伦特原油", "unit": "美元/桶",
                "frequency": "日度", "scope": "共享市场背景",
                "current": {"date": "2026-10-24", "value": 116.0},
            },
            "history": [
                {"date": "2026-10-21", "value": 110.0},
                {"date": "2026-10-24", "value": 116.0},
            ],
            "as_of": "2026-10-26",
        },
    )
    block = context.research_memory()["metric_block"]
    assert block["metrics"][0]["first"] == {"date": "2026-10-21", "value": 110.0}
    assert block["metrics"][0]["latest"] == {"date": "2026-10-24", "value": 116.0}
    assert block["metrics"][0]["unit"] == "美元/桶"
    assert block["metrics"][0]["lag_days"] == 2


def test_context_compacts_old_replaceable_previews(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    scope = HarnessScope("s1")
    store.create_run(run_id="run", answer_id="a", scope=scope, question="q", plan={}, budget=HarnessBudget(), model="m")
    context = ContextManager("run", store, "问题", "系统", {}, soft_tokens=100, max_tokens=160, output_reserve_tokens=30, preview_tokens=60)
    for index in range(4):
        context.add_tool_result(
            ToolEnvelope(f"c{index}", "geo_evidence_search", True, None),
            {"records": [{"id": f"r{index}", "text": "正文" * 200}]},
        )
    event = context.compact_if_needed()
    assert event is not None
    assert event.removed_previews == 3
    assert len(context.envelopes) == 1


def test_context_compacts_protected_previews_when_hard_limit_is_exceeded(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    scope = HarnessScope("s1")
    store.create_run(run_id="run", answer_id="a", scope=scope, question="q", plan={}, budget=HarnessBudget(), model="m")
    context = ContextManager(
        "run", store, "问题", "系统", {}, soft_tokens=120,
        max_tokens=220, output_reserve_tokens=40, preview_tokens=100,
    )
    for index in range(8):
        context.add_tool_result(
            ToolEnvelope(f"detail-{index}", "geo_evidence_get", True, None),
            {"record": {"id": f"r{index}", "text": "中文正文" * 300}},
        )
    event = context.compact_if_needed()
    assert event is not None
    assert event.after_tokens < event.before_tokens
    assert event.after_tokens < context.max_tokens - context.output_reserve_tokens
    assert any(
        isinstance(item.preview, dict) and item.preview.get("_preview", {}).get("compacted")
        for item in context.envelopes
    )
    restored = context.add_tool_result(
        ToolEnvelope("detail-0", "geo_evidence_get", True, None),
        {"record": {"id": "r0", "text": "新的大段正文" * 300}},
    )
    assert restored.preview.get("_preview", {}).get("compacted") is True


def test_context_hard_limit_wins_over_misconfigured_soft_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    store.create_run(
        run_id="hard-limit", answer_id="a", scope=HarnessScope("s1"), question="q",
        plan={}, budget=HarnessBudget(), model="m",
    )
    context = ContextManager(
        "hard-limit", store, "问题", "系统", {}, soft_tokens=10_000,
        max_tokens=220, output_reserve_tokens=80, preview_tokens=100,
    )
    for index in range(4):
        context.add_tool_result(
            ToolEnvelope(f"detail-{index}", "geo_evidence_get", True, None),
            {"record": {"id": f"r{index}", "text": "中文正文" * 300}},
        )
    assert context.current_tokens() > context.max_tokens - context.output_reserve_tokens
    event = context.compact_if_needed()
    assert event is not None
    assert event.after_tokens < event.before_tokens


def test_context_ledger_survives_compaction_and_research_memory_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    store.create_run(
        run_id="ledger-context", answer_id="a", scope=HarnessScope("s1"), question="q",
        plan={}, budget=HarnessBudget(), model="m",
    )
    context = ContextManager(
        "ledger-context", store, "问题", "系统", {}, soft_tokens=120,
        max_tokens=220, output_reserve_tokens=40, preview_tokens=80,
    )
    context.apply_ledger_updates([{
        "facet": "diplomacy", "claim": "仍存在接触窗口",
        "claim_type": "model_inference", "status": "inference",
        "supporting_ids": ["r1"], "full_text_ids": ["r1"],
    }])
    for index in range(5):
        context.add_tool_result(
            ToolEnvelope(f"detail-{index}", "geo_evidence_get", True, None),
            {"record": {"id": f"r{index}", "text": "中文正文" * 300}},
        )
    context.compact_if_needed()
    memory = context.research_memory()
    assert memory["ledger"]["entries"][0]["claim"] == "仍存在接触窗口"

    restored = ContextManager("ledger-context", store, "问题", "系统", {}, memory=memory)
    assert restored.research_memory()["ledger"] == memory["ledger"]

