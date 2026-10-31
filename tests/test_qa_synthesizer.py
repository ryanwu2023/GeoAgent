import asyncio
from datetime import datetime, timezone
import json

from backend.qa_claims import ClaimLedger, ClaimRecord, ClaimSpan, EvidenceRelation
from backend.qa_harness_state import HarnessScope
from backend.qa_plan_schema import TaskEvaluation, TaskProfile
from backend.qa_scope import ResearchScope
from backend.qa_synthesizer import synthesize_answer


def scope(question="最新美伊进展"):
    return ResearchScope.from_question(
        question, view_scope=HarnessScope("s1", "usiran"),
        snapshot_created_at="2026-11-01T00:00:00Z",
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )


def profile(intent="current_situation"):
    return TaskProfile(
        intent, "multi_domain", "deep", "latest", "high", "research", 0.9,
        requires_counterevidence=True, requires_metrics=intent == "asset_implication",
    )


def ledger():
    value = ClaimLedger()
    claim = value.upsert_claim(ClaimRecord.create(
        text="外交接触仍在继续，但军事行动尚未获得独立确认",
        information_nature="media_report", scope_version="research-scope/v1",
        facets=["diplomacy", "military"], verification="partial",
    ))
    value.add_relation(EvidenceRelation.create(
        claim_id=claim.claim_id, evidence_id="e1", relation="supports",
        evidence_nature="media_report", origin_group="wire-1",
        span=ClaimSpan(0, 2, "e1-v1", "外交"),
    ))
    return value, claim


def test_synthesis_prepends_business_answer_when_model_starts_with_internal_distribution():
    claims, claim = ledger()

    async def complete(messages, model_role):
        return json.dumps({
            "answer_markdown": "共有3540条记录、199个来源。\n\n## 进展\n外交接触仍在继续，但军事行动尚未获得独立确认。",
            "business_lead": "截至当前快照，证据以T3为主。",
            "claim_map": [{"claim_id": claim.claim_id, "quote": claim.text}],
        }, ensure_ascii=False), "model"

    draft = asyncio.run(synthesize_answer(
        "最新美伊进展", profile(), scope(), claims,
        [TaskEvaluation("T1", "all", "satisfied", claim_ids=[claim.claim_id])],
        complete=complete, budget={},
    ))
    first_paragraph = draft.markdown.split("\n\n", 1)[0]
    assert "外交接触仍在继续" in first_paragraph
    assert "条记录" not in first_paragraph
    assert draft.business_lead == first_paragraph
    assert draft.business_lead_claim_ids == [claim.claim_id]


def test_prompt_structure_changes_for_asset_question():
    claims, _ = ledger()
    captured = {}

    async def complete(messages, model_role):
        captured["system"] = messages[0]["content"]
        return "## 核心判断\n\n能源风险溢价取决于实际运输中断。", "model"

    asyncio.run(synthesize_answer(
        "美伊冲突对大类资产有什么影响", profile("asset_implication"),
        scope("美伊冲突对大类资产有什么影响"), claims, [], complete=complete, budget={},
    ))
    assert "传导机制" in captured["system"]
    assert "市场验证" in captured["system"]
    assert "失效条件" in captured["system"]


def test_model_failure_still_returns_nonempty_question_driven_chinese_answer():
    claims, claim = ledger()

    async def complete(*args, **kwargs):
        raise TimeoutError("model timeout")

    draft = asyncio.run(synthesize_answer(
        "最新美伊进展", profile(), scope(), claims,
        [TaskEvaluation("T1", "all", "satisfied", claim_ids=[claim.claim_id])],
        complete=complete, budget={},
    ))
    assert draft.markdown
    assert "美伊" in draft.markdown
    assert "外交接触仍在继续" in draft.markdown
    assert draft.status == "degraded"

