import pytest

from backend.qa_claims import (
    ClaimDependency,
    ClaimLedger,
    ClaimRecord,
    ClaimSpan,
    EvidenceRelation,
    NumericProvenance,
)


def claim(text="美国官员表示谈判仍在继续", *, nature="official_statement", scope="scope-v1", **kwargs):
    return ClaimRecord.create(text=text, information_nature=nature, scope_version=scope, **kwargs)


def test_claim_id_is_stable_and_includes_nature_and_scope_version():
    first = claim()
    assert first.claim_id == claim().claim_id
    assert first.claim_id != claim(nature="media_report").claim_id
    assert first.claim_id != claim(scope="scope-v2").claim_id


def test_official_statement_only_supports_that_the_statement_was_made():
    ledger = ClaimLedger()
    statement = ledger.upsert_claim(claim())
    ledger.add_relation(EvidenceRelation.create(
        claim_id=statement.claim_id,
        evidence_id="official-1",
        relation="supports",
        evidence_nature="official_statement",
        origin_group="white-house",
    ))
    assert ledger.independent_confirmation_count(statement.claim_id) == 1

    asserted_fact = ledger.upsert_claim(claim(
        "谈判已经取得实质进展", nature="verified_fact",
    ))
    with pytest.raises(ValueError, match="official_statement_cannot_verify_external_fact"):
        ledger.add_relation(EvidenceRelation.create(
            claim_id=asserted_fact.claim_id,
            evidence_id="official-1",
            relation="supports",
            evidence_nature="official_statement",
            origin_group="white-house",
        ))


def test_syndicated_reports_count_as_one_independent_confirmation():
    ledger = ClaimLedger()
    item = ledger.upsert_claim(claim("媒体报道称设施受损", nature="media_report"))
    for evidence_id in ("copy-a", "copy-b"):
        ledger.add_relation(EvidenceRelation.create(
            claim_id=item.claim_id,
            evidence_id=evidence_id,
            relation="supports",
            evidence_nature="media_report",
            origin_group="wire-story-1",
        ))
    assert ledger.independent_confirmation_count(item.claim_id) == 1


def test_one_evidence_relation_preserves_multiple_facets_and_conflict():
    ledger = ClaimLedger()
    item = ledger.upsert_claim(claim(
        "设施已经受损", nature="single_party_claim", facets=["military", "market"],
    ))
    ledger.add_relation(EvidenceRelation.create(
        claim_id=item.claim_id,
        evidence_id="claim-1",
        relation="supports",
        evidence_nature="single_party_claim",
        facets=["military", "market"],
        origin_group="party-a",
    ))
    ledger.add_relation(EvidenceRelation.create(
        claim_id=item.claim_id,
        evidence_id="denial-1",
        relation="contradicts",
        evidence_nature="official_statement",
        facets=["military", "verification"],
        origin_group="party-b",
    ))
    relations = ledger.relations_for(item.claim_id)
    assert relations[0].facets == ["military", "market"]
    assert {relation.relation for relation in relations} == {"supports", "contradicts"}
    assert ledger.claims[item.claim_id].verification == "conflicted"


def test_span_is_bound_to_python_character_offsets_and_content_version():
    body = "甲方表示：谈判仍在继续。"
    start = body.index("谈判")
    span = ClaimSpan(start=start, end=start + len("谈判仍在继续"), content_version="sha256:v1", quote="谈判仍在继续")
    assert span.extract(body, "sha256:v1") == "谈判仍在继续"
    with pytest.raises(ValueError, match="content_version_mismatch"):
        span.extract(body, "sha256:v2")


def test_program_calculation_records_inputs_formula_result_and_numeric_scope():
    number = NumericProvenance(
        value="3.14", unit="%", observed_at="2026-10-23",
        statistical_scope="单日收盘变动", input_series=["brent:t", "brent:t-1"],
        formula="(t / t-1 - 1) * 100", result="3.14",
    )
    item = claim(
        "布伦特原油单日上涨3.14%", nature="program_calculation",
        numeric_provenance=[number],
    )
    assert item.numeric_provenance[0].input_series == ["brent:t", "brent:t-1"]
    assert item.numeric_provenance[0].formula == "(t / t-1 - 1) * 100"
    assert item.numeric_provenance[0].result == "3.14"


def test_program_calculation_rejects_missing_formula_or_inputs():
    incomplete = NumericProvenance(value="3.14", unit="%", result="3.14")
    with pytest.raises(ValueError, match="program_calculation_requires_complete_provenance"):
        claim(
            "布伦特原油上涨3.14%", nature="program_calculation",
            numeric_provenance=[incomplete],
        )


def test_internal_message_cannot_upgrade_public_fact_to_verified():
    ledger = ClaimLedger()
    item = ledger.upsert_claim(claim("袭击已经发生", nature="verified_fact"))
    with pytest.raises(ValueError, match="internal_message_cannot_verify_public_fact"):
        ledger.add_relation(EvidenceRelation.create(
            claim_id=item.claim_id,
            evidence_id="internal-1",
            relation="supports",
            evidence_nature="internal_message",
            origin_group="ht-macro",
            is_internal=True,
        ))


def test_internal_message_is_not_an_independent_public_confirmation():
    ledger = ClaimLedger()
    item = ledger.upsert_claim(claim("内部消息提到谈判窗口", nature="internal_message"))
    ledger.add_relation(EvidenceRelation.create(
        claim_id=item.claim_id,
        evidence_id="internal-1",
        relation="supports",
        evidence_nature="internal_message",
        origin_group="ht-macro",
    ))
    assert ledger.independent_confirmation_count(item.claim_id) == 0


def test_dependency_invalidation_recursively_invalidates_inferences_only():
    ledger = ClaimLedger()
    premise = ledger.upsert_claim(claim("航运出现持续中断", nature="verified_fact"))
    inference = ledger.upsert_claim(claim("油价风险溢价可能上升", nature="model_inference"))
    downstream = ledger.upsert_claim(claim("能源权益相对受益", nature="model_inference"))
    independent = ledger.upsert_claim(claim("黄金价格走平", nature="program_calculation", numeric_provenance=[
        NumericProvenance(
            value="0", unit="%", observed_at="2026-10-23", statistical_scope="单日",
            input_series=["gold:t", "gold:t-1"], formula="(t/t-1-1)*100", result="0",
        )
    ]))
    ledger.add_dependency(ClaimDependency(premise.claim_id, inference.claim_id))
    ledger.add_dependency(ClaimDependency(inference.claim_id, downstream.claim_id))

    invalidated = ledger.invalidate_dependents(premise.claim_id)
    assert invalidated == [inference.claim_id, downstream.claim_id]
    assert ledger.claims[inference.claim_id].valid is False
    assert ledger.claims[downstream.claim_id].valid is False
    assert ledger.claims[independent.claim_id].valid is True


def test_claim_ledger_round_trip_preserves_relations_dependencies_and_spans():
    ledger = ClaimLedger()
    item = ledger.upsert_claim(claim())
    ledger.add_relation(EvidenceRelation.create(
        claim_id=item.claim_id,
        evidence_id="official-1",
        relation="supports",
        evidence_nature="official_statement",
        origin_group="official",
        span=ClaimSpan(0, 2, "v1", "声明"),
    ))
    restored = ClaimLedger.from_dict(ledger.to_dict())
    assert restored.to_dict() == ledger.to_dict()
