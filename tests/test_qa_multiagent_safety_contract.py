"""Safety contracts that become critical once one run has concurrent agents."""
from __future__ import annotations

import asyncio
import json

from backend.qa_harness import run_local_harness
from backend.qa_harness_state import HarnessBudget, HarnessEvent, HarnessScope
from backend.qa_harness_store import HarnessStore


class _MinimalTools:
    def __init__(self):
        self.snapshot = {"id": "snapshot-sequence"}
        self.topic = "usiran"
        self.dimension = ""
        self.start = ""
        self.end = ""
        self.retrieved_ids: list[str] = []
        self.full_text_ids: list[str] = []
        self.internal_message_ids: list[str] = []
        self.internal_full_text_ids: list[str] = []
        self.calls: list[dict] = []
        self.validation_facts: list[str] = []

    def call(self, name, arguments):
        self.calls.append({"tool": name, "arguments": arguments, "result_count": 0})
        if name == "geo_workspace_summary":
            return {"records_count": 0, "source_count": 0}
        return {"records": [], "returned": 0}

    def citations(self):
        return []

    def internal_sources(self):
        return []


async def _complete(*_args, **_kwargs):
    return json.dumps({
        "action": "final",
        "answer_markdown": "## 结论\n\n当前范围证据不足，需继续观察。",
        "citation_map": [],
        "gaps": ["缺少公开证据"],
    }, ensure_ascii=False), "test-model"


async def _publish(_item):
    return None


def test_resume_continues_after_max_persisted_sequence_instead_of_row_count(tmp_path, monkeypatch):
    """A sequence hole must not make resume overwrite an earlier audit step.

    ``qa_steps`` uses ``INSERT OR REPLACE``.  Deriving the next sequence from
    the number of rows is unsafe once concurrent delivery, migration or a
    partial cleanup leaves a gap.
    """
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    scope = HarnessScope("snapshot-sequence", "usiran")
    store.create_run(
        run_id="qa-sequence-hole",
        answer_id="answer-sequence-hole",
        scope=scope,
        question="当前美伊局势",
        plan={},
        budget=HarnessBudget(max_tool_calls=1),
        model="test-model",
    )
    store.append_step(
        "qa-sequence-hole",
        HarnessEvent(1, "planning", "stage", "completed", title="历史步骤一"),
    )
    store.append_step(
        "qa-sequence-hole",
        HarnessEvent(3, "planning", "stage", "completed", title="不得覆盖的历史步骤三"),
    )
    plan = {
        "focuses": ["事件主线"],
        "search_queries": [],
        "needs_metrics": False,
        "needs_internal_messages": False,
        "request_scope": {"start": "", "end": ""},
        "budget": {"evidence": 4, "full_text": 2, "search_page": 2, "tool_calls": 1},
        "multi_agent": {"enabled": False, "agents": []},
    }

    asyncio.run(run_local_harness(
        question="当前美伊局势",
        plan=plan,
        tools=_MinimalTools(),
        complete=_complete,
        publish=_publish,
        run_id="qa-sequence-hole",
        store=store,
    ))

    steps = store.list_steps("qa-sequence-hole")
    assert next(item for item in steps if item["sequence"] == 3)["title"] == "不得覆盖的历史步骤三"
    sequences = [item["sequence"] for item in steps]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert max(sequences) > 3

