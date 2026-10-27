import pytest

from backend.qa_ledger import LedgerEntry, ResearchLedger


def test_ledger_builds_stable_id_and_merges_evidence_without_double_counting():
    first = LedgerEntry.from_dict({
        "facet": "diplomacy",
        "claim": "美伊间接接触仍在进行",
        "claim_type": "media_report",
        "status": "reported",
        "published_at": "2026-10-24T02:00:00Z",
        "supporting_ids": ["r1"],
        "full_text_ids": ["r1"],
        "source_tiers": ["T2"],
        "independent_source_ids": ["xinhua"],
        "syndicated_source_ids": ["republisher-a"],
        "open_questions": ["是否有美方确认"],
    })
    duplicate = LedgerEntry.from_dict({
        "facet": " diplomacy ",
        "claim": " 美伊间接接触仍在进行 ",
        "claim_type": "media_report",
        "status": "reported",
        "published_at": "2026-10-25T03:00:00Z",
        "supporting_ids": ["r1", "r2"],
        "source_tiers": ["T2", "T3"],
        "independent_source_ids": ["xinhua", "official"],
        "syndicated_source_ids": ["republisher-a", "republisher-b"],
        "open_questions": ["是否有美方确认", "是否已经执行"],
    })
    assert first.ledger_id == duplicate.ledger_id

    ledger = ResearchLedger()
    ledger.merge_entry(first)
    merged = ledger.merge_entry(duplicate)
    assert merged.supporting_ids == ["r1", "r2"]
    assert merged.full_text_ids == ["r1"]
    assert merged.source_tiers == ["T2", "T3"]
    assert merged.independent_sources == 2
    assert merged.syndicated_sources == 2
    assert merged.open_questions == ["是否有美方确认", "是否已经执行"]
    assert merged.published_at == "2026-10-25T03:00:00Z"
    assert len(ledger) == 1


def test_ledger_preserves_conflicting_evidence_and_marks_claim_conflicted():
    ledger = ResearchLedger()
    ledger.merge_entry({
        "facet": "military", "claim": "设施已经受损",
        "claim_type": "single_party_claim", "status": "single_party",
        "supporting_ids": ["claim-1"],
    })
    entry = ledger.merge_entry({
        "facet": "military", "claim": "设施已经受损",
        "claim_type": "single_party_claim", "status": "reported",
        "conflicting_ids": ["denial-1"], "full_text_ids": ["denial-1"],
    })
    assert entry.status == "conflicted"
    assert entry.supporting_ids == ["claim-1"]
    assert entry.conflicting_ids == ["denial-1"]
    assert entry.full_text_ids == ["denial-1"]


def test_ledger_does_not_downgrade_verified_claim_or_accept_forged_identity():
    ledger = ResearchLedger()
    verified = ledger.merge_entry({
        "ledger_id": "forged", "facet": "policy", "claim": "制裁已经发布",
        "claim_type": "verified_fact", "status": "verified",
        "verification": "verified", "confidence": "high", "supporting_ids": ["official"],
    })
    merged = ledger.merge_entry({
        "facet": "policy", "claim": "制裁已经发布",
        "claim_type": "verified_fact", "status": "reported",
        "verification": "pending", "confidence": "limited", "supporting_ids": ["media"],
    })
    assert verified.ledger_id != "forged"
    assert merged.ledger_id == verified.ledger_id
    assert merged.status == "verified"
    assert merged.verification == "verified"
    assert merged.confidence == "high"


@pytest.mark.parametrize("field,value", [
    ("claim_type", "rumor"),
    ("status", "certain"),
])
def test_ledger_rejects_unknown_enums(field, value):
    payload = {
        "facet": "diplomacy", "claim": "测试主张",
        "claim_type": "media_report", "status": "reported",
    }
    payload[field] = value
    with pytest.raises(ValueError):
        LedgerEntry.from_dict(payload)


def test_ledger_round_trip_and_legacy_empty_payload():
    ledger = ResearchLedger.from_dict({"entries": [{
        "facet": "market", "claim": "油价上涨",
        "claim_type": "program_calculation", "status": "verified",
        "supporting_ids": ["metric:brent"],
    }]})
    restored = ResearchLedger.from_dict(ledger.to_dict())
    assert restored.to_dict() == ledger.to_dict()
    assert ResearchLedger.from_dict(None).to_dict() == {"version": 1, "entries": []}

