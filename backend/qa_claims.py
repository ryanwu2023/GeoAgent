"""主张级证据、数字口径与推论依赖账本。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable


INFORMATION_NATURES = {
    "verified_fact",
    "official_statement",
    "single_party_claim",
    "media_report",
    "program_calculation",
    "internal_message",
    "model_inference",
    "gap",
}
EVIDENCE_RELATIONS = {"supports", "contradicts", "background"}
VERIFICATION_STATES = {
    "pending", "single_source_pending", "partial", "verified", "conflicted", "invalidated",
}
CONTENT_KINDS = {"title", "summary", "truncated_text", "full_text", "structured_value"}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _labels(value: Iterable[Any] | None) -> list[str]:
    if value is None:
        return []
    return list(dict.fromkeys(label for item in value if (label := _text(item))))


def _canonical_text(value: str) -> str:
    return _text(unicodedata.normalize("NFKC", value)).casefold()


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class ClaimSpan:
    """正文中的 Python 字符偏移；偏移只能在相同内容版本内复用。"""

    start: int
    end: int
    content_version: str
    quote: str = ""
    content_kind: str = "full_text"

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("invalid_claim_span")
        if not _text(self.content_version):
            raise ValueError("claim_span_missing_content_version")
        if self.content_kind not in CONTENT_KINDS:
            raise ValueError(f"unknown_content_kind:{self.content_kind}")

    def extract(self, body: str, content_version: str) -> str:
        if content_version != self.content_version:
            raise ValueError("content_version_mismatch")
        if self.end > len(body):
            raise ValueError("claim_span_out_of_bounds")
        extracted = body[self.start:self.end]
        if self.quote and extracted != self.quote:
            raise ValueError("claim_span_quote_mismatch")
        return extracted

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ClaimSpan":
        return cls(
            start=int(value.get("start", -1)),
            end=int(value.get("end", -1)),
            content_version=_text(value.get("content_version")),
            quote=str(value.get("quote") or ""),
            content_kind=_text(value.get("content_kind")) or "full_text",
        )


@dataclass(frozen=True)
class NumericProvenance:
    """数字、观察口径，以及程序计算所使用的输入和公式。"""

    value: str
    unit: str = ""
    observed_at: str = ""
    statistical_scope: str = ""
    input_series: list[str] = field(default_factory=list)
    formula: str = ""
    result: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _text(self.value))
        object.__setattr__(self, "unit", _text(self.unit))
        object.__setattr__(self, "observed_at", _text(self.observed_at))
        object.__setattr__(self, "statistical_scope", _text(self.statistical_scope))
        object.__setattr__(self, "input_series", _labels(self.input_series))
        object.__setattr__(self, "formula", _text(self.formula))
        object.__setattr__(self, "result", _text(self.result))
        if not self.value:
            raise ValueError("numeric_provenance_missing_value")
        try:
            parsed = Decimal(self.value)
        except InvalidOperation as exc:
            raise ValueError("numeric_provenance_invalid_value") from exc
        if not parsed.is_finite():
            raise ValueError("numeric_provenance_invalid_value")

    @property
    def calculation_complete(self) -> bool:
        return bool(self.input_series and self.formula and self.result)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "NumericProvenance":
        return cls(
            value=str(value.get("value") or ""),
            unit=str(value.get("unit") or ""),
            observed_at=str(value.get("observed_at") or ""),
            statistical_scope=str(value.get("statistical_scope") or ""),
            input_series=list(value.get("input_series") or []),
            formula=str(value.get("formula") or ""),
            result=str(value.get("result") or ""),
        )


def stable_claim_id(text: str, information_nature: str, scope_version: str) -> str:
    return _stable_id("claim", {
        "text": _canonical_text(text),
        "information_nature": information_nature,
        "scope_version": _text(scope_version),
    })


@dataclass
class ClaimRecord:
    claim_id: str
    text: str
    information_nature: str
    scope_version: str
    facets: list[str] = field(default_factory=list)
    event_at: str = ""
    verification: str = "pending"
    numeric_provenance: list[NumericProvenance] = field(default_factory=list)
    valid: bool = True
    invalid_reason: str = ""

    def __post_init__(self) -> None:
        self.text = _text(self.text)
        self.scope_version = _text(self.scope_version)
        self.facets = _labels(self.facets)
        self.event_at = _text(self.event_at)
        self.invalid_reason = _text(self.invalid_reason)
        self.numeric_provenance = [
            item if isinstance(item, NumericProvenance) else NumericProvenance.from_dict(item)
            for item in self.numeric_provenance
        ]
        if not self.text or not self.scope_version:
            raise ValueError("claim_missing_text_or_scope")
        if self.information_nature not in INFORMATION_NATURES:
            raise ValueError(f"unknown_information_nature:{self.information_nature}")
        if self.verification not in VERIFICATION_STATES:
            raise ValueError(f"unknown_verification_state:{self.verification}")
        self.claim_id = stable_claim_id(self.text, self.information_nature, self.scope_version)
        if self.information_nature == "program_calculation":
            if not self.numeric_provenance or not all(
                item.calculation_complete for item in self.numeric_provenance
            ):
                raise ValueError("program_calculation_requires_complete_provenance")

    @classmethod
    def create(
        cls,
        *,
        text: str,
        information_nature: str,
        scope_version: str,
        facets: Iterable[str] | None = None,
        event_at: str = "",
        verification: str = "pending",
        numeric_provenance: Iterable[NumericProvenance | dict[str, Any]] | None = None,
    ) -> "ClaimRecord":
        return cls(
            claim_id="",
            text=text,
            information_nature=information_nature,
            scope_version=scope_version,
            facets=list(facets or []),
            event_at=event_at,
            verification=verification,
            numeric_provenance=list(numeric_provenance or []),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ClaimRecord":
        return cls(
            claim_id=str(value.get("claim_id") or ""),
            text=str(value.get("text") or ""),
            information_nature=str(value.get("information_nature") or ""),
            scope_version=str(value.get("scope_version") or ""),
            facets=list(value.get("facets") or []),
            event_at=str(value.get("event_at") or ""),
            verification=str(value.get("verification") or "pending"),
            numeric_provenance=list(value.get("numeric_provenance") or []),
            valid=bool(value.get("valid", True)),
            invalid_reason=str(value.get("invalid_reason") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceRelation:
    relation_id: str
    claim_id: str
    evidence_id: str
    relation: str
    evidence_nature: str
    origin_group: str
    facets: list[str] = field(default_factory=list)
    span: ClaimSpan | None = None
    is_internal: bool = False
    source_tier: str = ""

    def __post_init__(self) -> None:
        if self.relation not in EVIDENCE_RELATIONS:
            raise ValueError(f"unknown_evidence_relation:{self.relation}")
        if self.evidence_nature not in INFORMATION_NATURES:
            raise ValueError(f"unknown_evidence_nature:{self.evidence_nature}")
        if not _text(self.claim_id) or not _text(self.evidence_id):
            raise ValueError("evidence_relation_missing_identity")

    @classmethod
    def create(
        cls,
        *,
        claim_id: str,
        evidence_id: str,
        relation: str,
        evidence_nature: str,
        origin_group: str = "",
        facets: Iterable[str] | None = None,
        span: ClaimSpan | None = None,
        is_internal: bool = False,
        source_tier: str = "",
    ) -> "EvidenceRelation":
        normalized_facets = _labels(facets)
        normalized_origin = _text(origin_group) or _text(evidence_id)
        relation_id = _stable_id("relation", {
            "claim_id": claim_id,
            "evidence_id": evidence_id,
            "relation": relation,
            "content_version": span.content_version if span else "",
            "start": span.start if span else None,
            "end": span.end if span else None,
        })
        return cls(
            relation_id=relation_id,
            claim_id=_text(claim_id),
            evidence_id=_text(evidence_id),
            relation=relation,
            evidence_nature=evidence_nature,
            origin_group=normalized_origin,
            facets=normalized_facets,
            span=span,
            is_internal=bool(is_internal),
            source_tier=_text(source_tier),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceRelation":
        span_value = value.get("span")
        return cls.create(
            claim_id=str(value.get("claim_id") or ""),
            evidence_id=str(value.get("evidence_id") or ""),
            relation=str(value.get("relation") or ""),
            evidence_nature=str(value.get("evidence_nature") or ""),
            origin_group=str(value.get("origin_group") or ""),
            facets=list(value.get("facets") or []),
            span=ClaimSpan.from_dict(span_value) if isinstance(span_value, dict) else None,
            is_internal=bool(value.get("is_internal", False)),
            source_tier=str(value.get("source_tier") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClaimDependency:
    premise_claim_id: str
    dependent_claim_id: str
    relation: str = "premise"
    reason: str = ""

    def __post_init__(self) -> None:
        if not _text(self.premise_claim_id) or not _text(self.dependent_claim_id):
            raise ValueError("claim_dependency_missing_identity")
        if self.premise_claim_id == self.dependent_claim_id:
            raise ValueError("claim_dependency_self_reference")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ClaimDependency":
        return cls(
            premise_claim_id=str(value.get("premise_claim_id") or ""),
            dependent_claim_id=str(value.get("dependent_claim_id") or ""),
            relation=str(value.get("relation") or "premise"),
            reason=str(value.get("reason") or ""),
        )


class ClaimLedger:
    """保持原始证据不变，只在独立关系层记录模型判断。"""

    version = 1

    def __init__(
        self,
        claims: Iterable[ClaimRecord | dict[str, Any]] | None = None,
        relations: Iterable[EvidenceRelation | dict[str, Any]] | None = None,
        dependencies: Iterable[ClaimDependency | dict[str, Any]] | None = None,
    ) -> None:
        self.claims: dict[str, ClaimRecord] = {}
        self.relations: dict[str, EvidenceRelation] = {}
        self.dependencies: list[ClaimDependency] = []
        for item in claims or []:
            self.upsert_claim(item if isinstance(item, ClaimRecord) else ClaimRecord.from_dict(item))
        for item in relations or []:
            self.add_relation(item if isinstance(item, EvidenceRelation) else EvidenceRelation.from_dict(item))
        for item in dependencies or []:
            self.add_dependency(item if isinstance(item, ClaimDependency) else ClaimDependency.from_dict(item))

    def upsert_claim(self, claim: ClaimRecord) -> ClaimRecord:
        current = self.claims.get(claim.claim_id)
        if current is None:
            self.claims[claim.claim_id] = claim
            return claim
        current.facets = _labels([*current.facets, *claim.facets])
        numeric_items = {
            json.dumps(asdict(item), ensure_ascii=False, sort_keys=True): item
            for item in [*current.numeric_provenance, *claim.numeric_provenance]
        }
        current.numeric_provenance = list(numeric_items.values())
        if claim.event_at and (not current.event_at or claim.event_at > current.event_at):
            current.event_at = claim.event_at
        return current

    def add_relation(self, relation: EvidenceRelation) -> None:
        claim = self.claims.get(relation.claim_id)
        if claim is None:
            raise ValueError("evidence_relation_unknown_claim")
        if (
            relation.relation == "supports"
            and relation.evidence_nature == "official_statement"
            and claim.information_nature != "official_statement"
        ):
            raise ValueError("official_statement_cannot_verify_external_fact")
        if (
            relation.relation == "supports"
            and (relation.is_internal or relation.evidence_nature == "internal_message")
            and claim.information_nature == "verified_fact"
        ):
            raise ValueError("internal_message_cannot_verify_public_fact")
        self.relations[relation.relation_id] = relation
        if relation.relation == "contradicts":
            claim.verification = "conflicted"

    def add_dependency(self, dependency: ClaimDependency) -> None:
        if (
            dependency.premise_claim_id not in self.claims
            or dependency.dependent_claim_id not in self.claims
        ):
            raise ValueError("claim_dependency_unknown_claim")
        if dependency not in self.dependencies:
            self.dependencies.append(dependency)

    def relations_for(self, claim_id: str) -> list[EvidenceRelation]:
        return [relation for relation in self.relations.values() if relation.claim_id == claim_id]

    def independent_confirmation_count(self, claim_id: str) -> int:
        origins = {
            relation.origin_group
            for relation in self.relations_for(claim_id)
            if relation.relation == "supports"
            and not relation.is_internal
            and relation.evidence_nature != "internal_message"
        }
        return len(origins)

    def invalidate_dependents(self, claim_id: str) -> list[str]:
        invalidated: list[str] = []
        queue = [claim_id]
        seen = {claim_id}
        while queue:
            premise = queue.pop(0)
            for dependency in self.dependencies:
                dependent_id = dependency.dependent_claim_id
                if dependency.premise_claim_id != premise or dependent_id in seen:
                    continue
                seen.add(dependent_id)
                dependent = self.claims.get(dependent_id)
                if dependent is None:
                    continue
                dependent.valid = False
                dependent.verification = "invalidated"
                dependent.invalid_reason = f"premise_invalidated:{premise}"
                invalidated.append(dependent_id)
                queue.append(dependent_id)
        return invalidated

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "claims": [claim.to_dict() for claim in self.claims.values()],
            "relations": [relation.to_dict() for relation in self.relations.values()],
            "dependencies": [asdict(dependency) for dependency in self.dependencies],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ClaimLedger":
        if not isinstance(value, dict):
            return cls()
        return cls(
            claims=value.get("claims") if isinstance(value.get("claims"), list) else [],
            relations=value.get("relations") if isinstance(value.get("relations"), list) else [],
            dependencies=value.get("dependencies") if isinstance(value.get("dependencies"), list) else [],
        )
