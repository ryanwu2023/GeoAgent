from backend.qa_claims import ClaimLedger, ClaimRecord, ClaimSpan, EvidenceRelation
from backend.qa_evaluator import build_progress_fingerprint, evaluate_task
from backend.qa_harness_state import HarnessScope
from backend.qa_plan_schema import ResearchTask, TaskBudget
from backend.qa_scope import ResearchScope
from datetime import datetime, timezone


def scope():
    return ResearchScope.from_question(
        "最新美伊进展", view_scope=HarnessScope("s1", "usiran"),
        snapshot_created_at="2026-11-01T00:00:00Z",
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )


def task(criteria, *, task_type="evidence"):
    return ResearchTask(
        "T1", task_type, "核验是否发生实际军事部署",
        allowed_tool_categories=["public_evidence"],
        acceptance_criteria=list(criteria), scope_topics=["usiran"],
        budget=TaskBudget(),
    )


def add_claim(
    ledger, text, *, nature="media_report", verification="partial",
    relation="supports", origin="wire-1", evidence="e1", span=True,
):
    item = ledger.upsert_claim(ClaimRecord.create(
        text=text, information_nature=nature, scope_version=scope().version,
        facets=["military"], verification=verification,
    ))
    ledger.add_relation(EvidenceRelation.create(
        claim_id=item.claim_id, evidence_id=evidence, relation=relation,
        evidence_nature=nature, origin_group=origin,
        span=ClaimSpan(0, 2, f"v-{evidence}", "部署") if span else None,
    ))
    return item


def test_search_executed_without_full_text_cannot_pass_acceptance():
    ledger = ClaimLedger()
    claim = add_claim(ledger, "媒体报道称可能部署", span=False)
    work = task(["读取关键证据正文并核对实际部署"])
    work.claim_ids = [claim.claim_id]
    evaluation = evaluate_task(work, ledger, scope(), model_result={"search_executed": True})
    assert evaluation.result == "incomplete"
    assert any("正文" in gap for gap in evaluation.gaps)


def test_generic_candidate_without_text_span_is_not_treated_as_supported():
    ledger = ClaimLedger()
    claim = add_claim(ledger, "媒体报道候选线索", span=False)
    work = task(["取得能够回答任务目标的可追溯证据"])
    work.claim_ids = [claim.claim_id]
    evaluation = evaluate_task(work, ledger, scope())
    assert evaluation.result == "incomplete"
    assert evaluation.suggested_action == "full_text_validation"


def test_official_statement_only_partially_satisfies_actual_deployment():
    ledger = ClaimLedger()
    statement = add_claim(
        ledger, "国防部表示将部署兵力", nature="official_statement",
        verification="partial", evidence="official-1",
    )
    work = task(["确认实际部署已经发生"])
    work.claim_ids = [statement.claim_id]
    evaluation = evaluate_task(work, ledger, scope())
    assert evaluation.result == "incomplete"
    assert evaluation.suggested_action == "actual_action_verification"


def test_single_major_media_report_requires_independent_verification():
    ledger = ClaimLedger()
    claim = add_claim(ledger, "媒体称核设施遭到打击", nature="media_report")
    work = task(["核验重大军事行动"])
    work.claim_ids = [claim.claim_id]
    evaluation = evaluate_task(work, ledger, scope(), model_result={"major_claim_ids": [claim.claim_id]})
    assert evaluation.result == "incomplete"
    assert evaluation.suggested_action == "source_verification"


def test_conflicting_evidence_is_preserved_for_synthesis():
    ledger = ClaimLedger()
    claim = add_claim(ledger, "一方称设施受损", nature="media_report")
    ledger.add_relation(EvidenceRelation.create(
        claim_id=claim.claim_id, evidence_id="denial-1", relation="contradicts",
        evidence_nature="official_statement", origin_group="party-b",
        span=ClaimSpan(0, 2, "v-denial", "否认"),
    ))
    work = task(["核验设施是否受损"])
    work.claim_ids = [claim.claim_id]
    evaluation = evaluate_task(work, ledger, scope())
    assert evaluation.result == "conflicted"
    assert claim.claim_id in evaluation.claim_ids
    assert set(evaluation.evidence_ids) == {"e1", "denial-1"}


def test_metric_narrative_conflict_requests_targeted_follow_up():
    ledger = ClaimLedger()
    claim = add_claim(ledger, "报道宣称航运已经恢复", nature="media_report")
    work = task(["核对叙事与市场指标是否一致"], task_type="market_assessment")
    work.claim_ids = [claim.claim_id]
    evaluation = evaluate_task(
        work, ledger, scope(), model_result={"metric_narrative_conflict": "通行量仍处低位"},
    )
    assert evaluation.result == "conflicted"
    assert evaluation.suggested_action == "metric_conflict_verification"


def test_progress_counts_origin_groups_and_full_text_not_syndicated_hits():
    ledger = ClaimLedger()
    claim = add_claim(ledger, "报道一", evidence="copy-a", origin="wire-story")
    ledger.add_relation(EvidenceRelation.create(
        claim_id=claim.claim_id, evidence_id="copy-b", relation="supports",
        evidence_nature="media_report", origin_group="wire-story",
        span=ClaimSpan(0, 2, "v-copy-b", "报道"),
    ))
    progress = build_progress_fingerprint(ledger)
    assert progress.independent_origin_groups == ["wire-story"]
    assert set(progress.full_text_ids) == {"copy-a", "copy-b"}

