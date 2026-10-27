"""Structured claim and evidence memory for the local QA harness."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from typing import Any, Iterable


CLAIM_TYPES = {
    "verified_fact", "official_statement", "single_party_claim", "media_report",
    "program_calculation", "internal_message", "model_inference", "gap",
}
STATUSES = {"verified", "reported", "single_party", "inference", "conflicted", "gap"}
_STATUS_RANK = {"gap": 0, "inference": 1, "single_party": 2, "reported": 3, "verified": 4}
_CONFIDENCE_RANK = {"limited": 0, "low": 1, "medium": 2, "high": 3}
_VERIFICATION_RANK = {"pending": 0, "unverified": 1, "partial": 2, "verified": 3}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(_text(item) for item in value if _text(item)))


def _merge(left: Iterable[str], right: Iterable[str]) -> list[str]:
    return list(dict.fromkeys([*left, *right]))


def stable_ledger_id(facet: str, claim: str, claim_type: str) -> str:
    payload = json.dumps(
        {"facet": _text(facet).lower(), "claim": _text(claim).lower(), "claim_type": claim_type},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return "ledger-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


@dataclass
class LedgerEntry:
    ledger_id: str
    facet: str
    claim: str
    claim_type: str
    status: str
    event_at: str | None = None
    published_at: str | None = None
    supporting_ids: list[str] = field(default_factory=list)
    conflicting_ids: list[str] = field(default_factory=list)
    full_text_ids: list[str] = field(default_factory=list)
    source_tiers: list[str] = field(default_factory=list)
    independent_sources: int = 0
    syndicated_sources: int = 0
    independent_source_ids: list[str] = field(default_factory=list)
    syndicated_source_ids: list[str] = field(default_factory=list)
    verification: str = "pending"
    confidence: str = "limited"
    agent_role: str = ""
    open_questions: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LedgerEntry":
        if not isinstance(value, dict):
            raise ValueError("ledger_entry_not_object")
        facet, claim = _text(value.get("facet")), _text(value.get("claim"))
        claim_type, status = _text(value.get("claim_type")), _text(value.get("status"))
        if not facet or not claim:
            raise ValueError("ledger_entry_missing_claim")
        if claim_type not in CLAIM_TYPES:
            raise ValueError(f"unknown_claim_type:{claim_type}")
        if status not in STATUSES:
            raise ValueError(f"unknown_claim_status:{status}")
        independent_ids = _strings(value.get("independent_source_ids"))
        syndicated_ids = _strings(value.get("syndicated_source_ids"))
        independent_count = max(int(value.get("independent_sources") or 0), len(independent_ids))
        syndicated_count = max(int(value.get("syndicated_sources") or 0), len(syndicated_ids))
        return cls(
            ledger_id=stable_ledger_id(facet, claim, claim_type),
            facet=facet, claim=claim, claim_type=claim_type, status=status,
            event_at=_text(value.get("event_at")) or None,
            published_at=_text(value.get("published_at")) or None,
            supporting_ids=_strings(value.get("supporting_ids")),
            conflicting_ids=_strings(value.get("conflicting_ids")),
            full_text_ids=_strings(value.get("full_text_ids")),
            source_tiers=_strings(value.get("source_tiers")),
            independent_sources=independent_count,
            syndicated_sources=syndicated_count,
            independent_source_ids=independent_ids,
            syndicated_source_ids=syndicated_ids,
            verification=_text(value.get("verification")) or "pending",
            confidence=_text(value.get("confidence")) or "limited",
            agent_role=_text(value.get("agent_role")),
            open_questions=_strings(value.get("open_questions")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResearchLedger:
    """Deduplicated, JSON-safe collection of research claims."""

    version = 1

    def __init__(self, entries: Iterable[LedgerEntry | dict[str, Any]] | None = None):
        self._entries: dict[str, LedgerEntry] = {}
        for entry in entries or []:
            self.merge_entry(entry)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[LedgerEntry]:
        return list(self._entries.values())

    def merge_entry(self, value: LedgerEntry | dict[str, Any]) -> LedgerEntry:
        incoming = value if isinstance(value, LedgerEntry) else LedgerEntry.from_dict(value)
        current = self._entries.get(incoming.ledger_id)
        if current is None:
            self._entries[incoming.ledger_id] = incoming
            return incoming
        current.supporting_ids = _merge(current.supporting_ids, incoming.supporting_ids)
        current.conflicting_ids = _merge(current.conflicting_ids, incoming.conflicting_ids)
        current.full_text_ids = _merge(current.full_text_ids, incoming.full_text_ids)
        current.source_tiers = _merge(current.source_tiers, incoming.source_tiers)
        current.independent_source_ids = _merge(current.independent_source_ids, incoming.independent_source_ids)
        current.syndicated_source_ids = _merge(current.syndicated_source_ids, incoming.syndicated_source_ids)
        current.independent_sources = max(
            current.independent_sources, incoming.independent_sources, len(current.independent_source_ids),
        )
        current.syndicated_sources = max(
            current.syndicated_sources, incoming.syndicated_sources, len(current.syndicated_source_ids),
        )
        current.open_questions = _merge(current.open_questions, incoming.open_questions)
        current.event_at = max(filter(None, (current.event_at, incoming.event_at)), default=None)
        current.published_at = max(filter(None, (current.published_at, incoming.published_at)), default=None)
        if current.conflicting_ids or incoming.status == "conflicted":
            current.status = "conflicted"
        elif _STATUS_RANK.get(incoming.status, -1) > _STATUS_RANK.get(current.status, -1):
            current.status = incoming.status
        if _VERIFICATION_RANK.get(incoming.verification, 0) > _VERIFICATION_RANK.get(current.verification, 0):
            current.verification = incoming.verification
        if _CONFIDENCE_RANK.get(incoming.confidence, 0) > _CONFIDENCE_RANK.get(current.confidence, 0):
            current.confidence = incoming.confidence
        if incoming.agent_role:
            current.agent_role = incoming.agent_role
        return current

    def merge_entries(self, values: Iterable[LedgerEntry | dict[str, Any]]) -> list[LedgerEntry]:
        return [self.merge_entry(value) for value in values]

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "entries": [entry.to_dict() for entry in self.entries]}

    @classmethod
    def from_dict(cls, value: Any) -> "ResearchLedger":
        if not isinstance(value, dict):
            return cls()
        entries = value.get("entries") if isinstance(value.get("entries"), list) else []
        return cls(entries)

