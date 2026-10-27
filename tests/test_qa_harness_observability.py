"""Cross-cutting acceptance tests for the auditable local QA tool loop.

These tests deliberately exercise contracts spanning the scheduler, persisted
run state, progress stream and detail lookup.  They do not assert presentation
copy, so the UI can evolve without weakening the security/audit boundary.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json

from backend.qa_agent import TOOL_GUIDE, TOOL_NAMES
from backend.qa_harness import run_local_harness
from backend.qa_harness_state import HarnessBudget, HarnessRun, HarnessScope, ToolCallState
from backend.qa_harness_store import HarnessStore


EXPECTED_TOOLS = {
    "geo_workspace_summary",
    "geo_evidence_search",
    "geo_evidence_get",
    "geo_metric_catalog",
    "geo_metric_series",
    "geo_analysis_view",
    "geo_internal_message_stats",
    "geo_internal_message_search",
    "geo_internal_message_get",
}


class TraceTools:
    def __init__(self, *, fail_search: bool = False):
        self.snapshot = {"id": "snapshot-1"}
        self.topic = "usiran"
        self.dimension = ""
        self.start = "2026-10-20"
        self.end = "2026-10-24"
        self.retrieved_ids: list[str] = []
        self.full_text_ids: list[str] = []
        self.internal_message_ids: list[str] = []
        self.internal_full_text_ids: list[str] = []
        self.record = {
            "id": "evidence-1",
            "title": "公开报道",
            "source_name": "测试来源",
            "published_at": "2026-10-24T01:00:00Z",
            "text": "FULL_ARTIFACT_BODY_SHOULD_NOT_BE_IN_PROGRESS_STREAM",
            "local_path": r"D:\private folder\secret.json",
            "posix_path": "/Users/private/My Documents/secret.json",
            "unc_path": r"\\internal-host\private share\secret.json",
            "metadata": {"api_key": "artifact-secret"},
        }
        self.record_by_id = {self.record["id"]: self.record}
        self.validation_facts = []
        self.calls: list[dict] = []
        self.fail_search = fail_search

    def call(self, name, arguments):
        self.calls.append({"tool": name, "arguments": arguments, "result_count": 1})
        if name == "geo_workspace_summary":
            return {"records_count": 1, "source_count": 1}
        if name == "geo_evidence_search":
            if self.fail_search:
                raise RuntimeError("上游暂时不可用 Authorization: Bearer should-not-leak")
            self.retrieved_ids = [self.record["id"]]
            return {"records": [{"id": self.record["id"], "title": self.record["title"]}], "returned": 1}
        if name == "geo_evidence_get":
            self.full_text_ids = [self.record["id"]]
            return {"record": self.record}
        return {"records": []}

    def citations(self):
        return [self.record] if self.retrieved_ids else []

    def internal_sources(self):
        return []


def _plan():
    return {
        "focuses": ["最新进展"],
        "search_queries": ["美伊 最新进展", "霍尔木兹 通行"],
        "needs_metrics": False,
        "needs_internal_messages": False,
        "request_scope": {"start": "2026-10-20", "end": "2026-10-24"},
        "budget": {"evidence": 24, "full_text": 8, "search_page": 8, "tool_calls": 4},
    }


async def _publish(target, item):
    target.append(item)


async def _final(*_args, **_kwargs):
    return json.dumps(
        {
            "action": "final",
            "answer_markdown": "## 结论\n\n当前公开资料显示局势仍需观察〔S1〕。",
            "citation_map": [{"anchor": "S1", "evidence_ids": ["evidence-1"]}],
            "gaps": [],
        },
        ensure_ascii=False,
    ), "test-model"


def _run(tmp_path, monkeypatch, *, fail_search=False):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    progress: list[dict] = []
    store = HarnessStore()
    outcome = asyncio.run(
        run_local_harness(
            question="最新美伊进展",
            plan=_plan(),
            tools=TraceTools(fail_search=fail_search),
            complete=_final,
            publish=lambda item: _publish(progress, item),
            run_id="qa-observability",
            store=store,
        )
    )
    return outcome, progress, store


def test_each_tool_lifecycle_merges_on_one_call_id_and_sequences_are_monotonic(tmp_path, monkeypatch):
    _, progress, _ = _run(tmp_path, monkeypatch)
    tool_events = [event for event in progress if event.get("kind") == "tool"]
    assert tool_events
    sequences = [event["sequence"] for event in progress]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))

    by_call: dict[str, list[dict]] = {}
    for event in tool_events:
        by_call.setdefault(event["call_id"], []).append(event)
        assert event["event_version"] == "tool-trace/v1"
        assert event["run_id"] == "qa-observability"
        assert event["mcp_server"] == "geoagent"
    for events in by_call.values():
        assert [event["status"] for event in events] == ["running", "completed"]
        assert events[0]["call_id"] == events[1]["call_id"]
        assert events[0]["started_at"] == events[1]["started_at"]
        assert events[1]["finished_at"]


def test_parallel_batch_calls_keep_independent_lifecycles(tmp_path, monkeypatch):
    _, progress, _ = _run(tmp_path, monkeypatch)
    tool_events = [event for event in progress if event.get("kind") == "tool"]
    bootstrap = {
        event["call_id"]: event
        for event in tool_events
        if event.get("phase") == "discovering" and event.get("status") == "completed"
    }
    assert {"bootstrap-workspace", "bootstrap-search-1", "bootstrap-search-2"} <= set(bootstrap)
    assert len({event["sequence"] for event in bootstrap.values()}) == len(bootstrap)
    assert all(event["tool"] for event in bootstrap.values())


def test_tool_failure_is_attributed_and_does_not_erase_other_success(tmp_path, monkeypatch):
    outcome, progress, _ = _run(tmp_path, monkeypatch, fail_search=True)
    terminals = [
        event for event in progress
        if event.get("kind") == "tool" and event.get("status") in {"completed", "degraded", "failed"}
    ]
    assert any(event["tool"] == "geo_workspace_summary" and event["status"] == "completed" for event in terminals)
    failed = [event for event in terminals if event["tool"] == "geo_evidence_search"]
    assert failed
    for event in failed:
        assert event["status"] == "degraded"
        assert event["error"]["source"] == "geoagent/geo_evidence_search"
        assert event["error"]["kind"] == "tool_unavailable"
        assert "should-not-leak" not in json.dumps(event, ensure_ascii=False)
    assert outcome["final"]["text"]


def test_progress_stream_is_summary_only_while_artifact_remains_persisted(tmp_path, monkeypatch):
    _, progress, store = _run(tmp_path, monkeypatch)
    serialized = json.dumps(progress, ensure_ascii=False)
    assert "FULL_ARTIFACT_BODY_SHOULD_NOT_BE_IN_PROGRESS_STREAM" not in serialized
    assert '"payload"' not in serialized
    assert '"artifact"' not in serialized

    run = store.load_run("qa-observability")
    assert run is not None
    read_state = next(state for state in run.tool_calls.values() if state.tool == "geo_evidence_get")
    artifact = store.load_artifact(read_state.artifact_id)
    assert artifact["record"]["text"] == "FULL_ARTIFACT_BODY_SHOULD_NOT_BE_IN_PROGRESS_STREAM"


def test_tool_detail_rejects_cross_run_call_and_redacts_all_three_boundaries(tmp_path, monkeypatch):
    _, progress, store = _run(tmp_path, monkeypatch)
    other = store.create_run(
        run_id="qa-other",
        answer_id="answer-other",
        scope=HarnessScope("snapshot-1", "usiran"),
        question="其他问题",
        plan={},
        budget=HarnessBudget(),
        model="test",
    )
    other.tool_calls["foreign-call"] = ToolCallState(
        call_id="foreign-call",
        tool="geo_evidence_search",
        arguments={"Authorization": "Bearer foreign-secret"},
        status="completed",
    )
    store.save_checkpoint(other)

    assert store.get_tool_call_detail("qa-observability", "foreign-call") is None
    run = store.load_run("qa-observability")
    assert run is not None
    call_id = next(state.call_id for state in run.tool_calls.values() if state.tool == "geo_evidence_get")
    detail = store.get_tool_call_detail("qa-observability", call_id)
    assert detail is not None
    encoded = json.dumps(detail, ensure_ascii=False)
    assert "Bearer " not in encoded
    assert "foreign-secret" not in encoded
    assert "artifact-secret" not in encoded
    assert "private" not in encoded
    assert "folder" not in detail["artifact"]["payload"]["record"]["local_path"]
    assert "Documents" not in detail["artifact"]["payload"]["record"]["posix_path"]
    assert "internal-host" not in detail["artifact"]["payload"]["record"]["unc_path"]
    assert "D:\\" not in encoded and "C:\\" not in encoded
    assert detail["run_id"] == "qa-observability"
    assert detail["call_id"] == call_id
    assert detail["artifact"]["payload"]["record"]["text"]

    # The streamed projection and persisted step projection are both summaries.
    persisted = store.list_steps("qa-observability")
    assert "FULL_ARTIFACT_BODY_SHOULD_NOT_BE_IN_PROGRESS_STREAM" not in json.dumps(persisted, ensure_ascii=False)
    assert all("payload" not in event for event in progress)


def test_legacy_run_without_tool_trace_fields_still_deserializes():
    legacy = {
        "run_id": "legacy-run",
        "answer_id": "legacy-answer",
        "question": "旧问题",
        "scope": {"snapshot_id": "snapshot-old"},
        "phase": "planning",
        "tool_calls": {
            "legacy-call": {
                "call_id": "legacy-call",
                "tool": "geo_workspace_summary",
                "arguments": {},
                "status": "completed",
                "attempts": 1,
                "artifact_id": None,
                "result_count": 1,
                "duration_ms": 2,
                "error": None,
            }
        },
    }
    run = HarnessRun.from_dict(legacy)
    state = run.tool_calls["legacy-call"]
    assert state.tool == "geo_workspace_summary"
    assert state.mcp_server == "geoagent"
    assert state.arguments_summary == {}


def test_model_format_error_is_audited_then_recovers_to_a_final_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    progress: list[dict] = []
    calls = 0

    async def malformed_then_valid(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "这既不是 JSON，也不是足以成为最终答案的 Markdown。", "test-model"
        return await _final()

    outcome = asyncio.run(
        run_local_harness(
            question="最新美伊进展",
            plan=_plan(),
            tools=TraceTools(),
            complete=malformed_then_valid,
            publish=lambda item: _publish(progress, item),
            run_id="qa-format-retry",
            store=HarnessStore(),
        )
    )
    assert calls == 2
    assert outcome["status"] == "completed"
    retry = next(event for event in progress if event.get("kind") == "model" and event.get("status") == "degraded")
    assert retry["retry_count"] == 1
    assert "格式" in retry["summary"]


def test_pichat_compatibility_boundary_and_nine_tool_contract_remain_explicit():
    assert TOOL_NAMES == EXPECTED_TOOLS
    assert {item["name"] for item in TOOL_GUIDE} == EXPECTED_TOOLS

    # This audit guard intentionally checks only the routing boundary.  PiChat
    # owns its own tool loop; local run IDs/events must not be introduced into
    # that provider branch.
    from pathlib import Path

    app_source = Path("backend/app.py").read_text(encoding="utf-8")
    assert 'model_status().get("provider") != "pichat"' in app_source

