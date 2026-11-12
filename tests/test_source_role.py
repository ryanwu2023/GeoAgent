from backend.adapters import normalize, party_official_sources
from backend.qa_retrieval import _tier_score
from backend.research import _brief_relevance


def test_party_state_media_is_tagged_from_collector_config():
    sources = party_official_sources()
    assert ("diplomacy-monitor", "app-pk") in sources
    assert ("readiness-monitor", "irna-en") in sources
    assert ("iran-escalation-monitor", "irna-en") in sources
    assert ("diplomacy-monitor", "un-press") not in sources


def test_archived_record_without_field_inherits_role_and_neutral_record_stays_untagged():
    # Raw lines archived before the field existed still map to their source config.
    app = normalize({"source_id": "app-pk", "title": "Pakistan says talks resume", "tier": "T1"}, "diplomacy-monitor")
    un = normalize({"source_id": "un-press", "title": "UN welcomes talks", "tier": "T1"}, "diplomacy-monitor")
    assert app["source_role"] == "party_official"
    assert "source_role" not in un
    explicit = normalize({"source_id": "x", "title": "t", "tier": "T1", "source_role": "party_official"}, "naval-monitor")
    assert explicit["source_role"] == "party_official"


def test_party_official_t1_ranks_below_neutral_t1_but_above_t2():
    neutral = _tier_score({"tier": "T1"})
    party = _tier_score({"tier": "T1", "source_role": "party_official"})
    assert neutral == 1.0
    assert _tier_score({"tier": "T2"}) < party < neutral


def test_brief_relevance_gives_party_official_only_t2_bonus():
    base = {"title": "Iran strikes US base, US deploys carrier to Hormuz", "text": ""}
    neutral = _brief_relevance({**base, "tier": "T1"}, "usiran")
    party = _brief_relevance({**base, "tier": "T1", "source_role": "party_official"}, "usiran")
    t2 = _brief_relevance({**base, "tier": "T2"}, "usiran")
    assert neutral is not None and party == t2 == neutral - 1
