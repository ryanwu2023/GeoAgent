"""Token-aware context and stable tool-result previews for the QA harness."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
import re
from typing import Any

from .qa_harness_store import HarnessStore
from .qa_ledger import ResearchLedger
from .qa_metrics import build_metric_block


_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_PRIORITY_KEYS = (
    "id", "title", "name", "series", "source", "source_name", "tier", "url",
    "published_at", "collected_at", "observed_at", "date", "unit", "current",
    "previous", "delta", "pct", "returned", "total", "record_count", "boundary",
)


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    cjk = len(_CJK.findall(text))
    return max(1, cjk + math.ceil((len(text) - cjk) / 4))


def _text_preview(value: str, budget: int) -> str:
    if estimate_tokens(value) <= budget:
        return value
    # Preserve Unicode boundaries and honor the actual token estimator.  A
    # simple character multiplier badly overshoots for CJK text, where one
    # character is deliberately estimated as one token.
    suffix = "…[预览已截断]"
    available = max(0, budget - estimate_tokens(suffix))
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(value[:middle]) <= available:
            low = middle
        else:
            high = middle - 1
    return value[:low].rstrip() + suffix


def build_preview(payload: Any, max_tokens: int = 1800) -> Any:
    """Create a valid JSON-compatible preview without slicing serialized JSON."""
    if estimate_tokens(payload) <= max_tokens:
        return payload
    if isinstance(payload, str):
        return _text_preview(payload, max_tokens)
    if isinstance(payload, list):
        items: list[Any] = []
        remaining = max(32, max_tokens - 24)
        for item in payload:
            preview = build_preview(item, max(24, min(remaining, max_tokens // 3)))
            if items and estimate_tokens(items + [preview]) > remaining:
                break
            items.append(preview)
        return {"items": items, "total": len(payload), "truncated": len(items) < len(payload)}
    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        keys = [key for key in _PRIORITY_KEYS if key in payload]
        keys += [str(key) for key in payload if str(key) not in keys]
        for key in keys:
            value = payload.get(key)
            item_budget = max(24, min(max_tokens // 3, max_tokens - estimate_tokens(result) - 16))
            if item_budget <= 24 and result:
                break
            preview = build_preview(value, item_budget)
            candidate = {**result, key: preview}
            if result and estimate_tokens(candidate) > max_tokens:
                break
            result[key] = preview
        omitted = max(0, len(payload) - len(result))
        if omitted:
            result["_preview"] = {"truncated": True, "omitted_fields": omitted}
        return result
    return payload


@dataclass
class ToolEnvelope:
    call_id: str
    tool: str
    ok: bool
    preview: Any
    artifact_id: str | None = None
    record_count: int | None = None
    duration_ms: int = 0
    error: dict[str, Any] | None = None


@dataclass
class CompactionEvent:
    before_tokens: int
    after_tokens: int
    removed_previews: int
    stop_discovery: bool


@dataclass
class ContextManager:
    run_id: str
    store: HarnessStore
    question: str
    system_prompt: str
    plan: dict[str, Any]
    max_tokens: int = 32000
    soft_tokens: int = 24000
    output_reserve_tokens: int = 6000
    preview_tokens: int = 1800
    envelopes: list[ToolEnvelope] = field(default_factory=list)
    memory: dict[str, Any] = field(default_factory=lambda: {
        "confirmed_facts": [], "conflicting_claims": [], "metrics": [], "counter_evidence": [],
        "evidence_ids": [], "internal_message_ids": [], "gaps": [], "open_questions": [],
        "metric_series_results": [], "metric_block": {"metrics": [], "basis": ""},
        "ledger": {"version": 1, "entries": []},
    })
    stop_discovery: bool = False
    _stable_previews: dict[str, Any] = field(default_factory=dict)
    _stable_success: dict[str, bool] = field(default_factory=dict)
    ledger: ResearchLedger = field(default_factory=ResearchLedger, init=False, repr=False)

    def __post_init__(self) -> None:
        self.load_research_memory(self.memory)

    def load_research_memory(self, value: dict[str, Any] | None) -> None:
        """Restore compact research memory, including legacy runs without a ledger."""
        restored = json.loads(json.dumps(value or {}, ensure_ascii=False))
        defaults = {
            "confirmed_facts": [], "conflicting_claims": [], "metrics": [], "counter_evidence": [],
            "evidence_ids": [], "internal_message_ids": [], "gaps": [], "open_questions": [],
            "metric_series_results": [], "metric_block": {"metrics": [], "basis": ""},
        }
        defaults.update(restored)
        self.ledger = ResearchLedger.from_dict(defaults.get("ledger"))
        defaults["ledger"] = self.ledger.to_dict()
        self.memory = defaults

    def apply_ledger_updates(self, updates: list[dict[str, Any]]) -> None:
        self.ledger.merge_entries(updates)
        self.memory["ledger"] = self.ledger.to_dict()

    def add_tool_result(self, envelope: ToolEnvelope, full_payload: Any) -> ToolEnvelope:
        preview = self._stable_previews.get(envelope.call_id)
        previous_ok = self._stable_success.get(envelope.call_id)
        # A failed call is only a transient observation.  If the same stable
        # call succeeds on a later attempt or resumed run, replace the error
        # preview with the successful result.  Successful previews remain
        # stable so model retries cannot silently change their evidence.
        if preview is None or (envelope.ok and previous_ok is False):
            preview = build_preview(full_payload, self.preview_tokens)
            self._stable_previews[envelope.call_id] = preview
            self._stable_success[envelope.call_id] = envelope.ok
        elif previous_ok is None:
            self._stable_success[envelope.call_id] = envelope.ok
        envelope.preview = preview
        if envelope.artifact_id is None:
            envelope.artifact_id = self.store.save_artifact(
                self.run_id, envelope.call_id, full_payload, preview, estimate_tokens(full_payload)
            )
        self.envelopes = [item for item in self.envelopes if item.call_id != envelope.call_id]
        self.envelopes.append(envelope)
        self._update_memory(envelope, full_payload)
        return envelope

    def _update_memory(self, envelope: ToolEnvelope, full_payload: Any) -> None:
        preview = envelope.preview
        if not isinstance(preview, dict):
            return
        records = preview.get("records")
        if isinstance(records, list):
            ids = [item.get("id") for item in records if isinstance(item, dict) and item.get("id")]
            self.memory["evidence_ids"] = list(dict.fromkeys(self.memory["evidence_ids"] + ids))
        elif isinstance(records, dict) and isinstance(records.get("items"), list):
            ids = [item.get("id") for item in records["items"] if isinstance(item, dict) and item.get("id")]
            self.memory["evidence_ids"] = list(dict.fromkeys(self.memory["evidence_ids"] + ids))
        messages = preview.get("messages")
        message_items = messages.get("items", []) if isinstance(messages, dict) else messages
        if isinstance(message_items, list):
            ids = [item.get("id") for item in message_items if isinstance(item, dict) and item.get("id")]
            self.memory["internal_message_ids"] = list(dict.fromkeys(self.memory["internal_message_ids"] + ids))
        # Keep compact business facts from aggregate tools in research memory.
        # Their list previews may be truncated, while the final synthesis needs
        # the complete metric combination and signal denominator.
        payload = full_payload if isinstance(full_payload, dict) else {}
        if envelope.tool == "geo_workspace_summary":
            self.memory["scope_summary"] = {
                key: payload.get(key) for key in (
                    "query_scope", "records_count", "source_count", "signal_counts",
                    "signal_basis", "metrics_count",
                )
            }
        elif envelope.tool == "geo_metric_catalog":
            values = payload.get("metrics") if isinstance(payload.get("metrics"), list) else []
            self.memory["metrics"] = [
                {key: item.get(key) for key in (
                    "series", "name", "unit", "delta_unit", "frequency", "scope", "current",
                    "previous", "delta", "pct", "freshness", "source_ids",
                )}
                for item in values[:20] if isinstance(item, dict)
            ]
            self.memory["metric_availability"] = [
                {
                    "series": item.get("series"), "name": item.get("name"),
                    "date": item.get("current", {}).get("date") if isinstance(item.get("current"), dict) else None,
                    "value": item.get("current", {}).get("value") if isinstance(item.get("current"), dict) else item.get("current"),
                }
                for item in values[:20]
                if isinstance(item, dict)
                and (
                    (isinstance(item.get("current"), dict) and item.get("current", {}).get("value") is not None)
                    or (not isinstance(item.get("current"), dict) and item.get("current") is not None)
                )
            ]
        elif envelope.tool == "geo_metric_series":
            metric = payload.get("metric") if isinstance(payload.get("metric"), dict) else {}
            series = str(metric.get("series") or "").strip()
            if series:
                # Keep a bounded, deterministic copy in research memory. The
                # complete receipt remains in the artifact store, while final
                # synthesis receives enough points for path statistics.
                compact = {
                    "metric": {
                        key: metric.get(key) for key in (
                            "series", "name", "unit", "frequency", "freshness",
                            "current", "scope", "boundary",
                        )
                    },
                    "history": [
                        {"date": item.get("date"), "value": item.get("value")}
                        for item in (
                            payload.get("history") if isinstance(payload.get("history"), list)
                            else metric.get("history") if isinstance(metric.get("history"), list)
                            else []
                        )[-50:]
                        if isinstance(item, dict)
                    ],
                    "as_of": payload.get("as_of"),
                }
                previous = self.memory.get("metric_series_results")
                values = list(previous) if isinstance(previous, list) else []
                values = [
                    item for item in values
                    if not (
                        isinstance(item, dict)
                        and isinstance(item.get("metric"), dict)
                        and str(item["metric"].get("series") or "") == series
                    )
                ]
                values.append(compact)
                # Retain the complete topic basket selected by the harness.
                # Six rows caused later secondary series (for example VIX/XLE)
                # to evict Brent, Hormuz and TACO from the final table.
                self.memory["metric_series_results"] = values[-10:]
                self.memory["metric_block"] = build_metric_block(self.memory["metric_series_results"])
        elif envelope.tool == "geo_analysis_view":
            self.memory["analysis_view"] = {
                "assets": payload.get("assets", [])[:8] if isinstance(payload.get("assets"), list) else [],
                "scenarios": payload.get("scenarios", [])[:6] if isinstance(payload.get("scenarios"), list) else [],
                "data_warnings": payload.get("data_warnings", [])[:10] if isinstance(payload.get("data_warnings"), list) else [],
                "basis": payload.get("basis"),
            }

    def research_memory(self) -> dict[str, Any]:
        self.memory["ledger"] = self.ledger.to_dict()
        return json.loads(json.dumps(self.memory, ensure_ascii=False))

    def _base_payload(self, stage: str) -> dict[str, Any]:
        self.memory["ledger"] = self.ledger.to_dict()
        return {
            "question": self.question,
            "plan": self.plan,
            "stage": stage,
            "research_memory": self.memory,
            "tool_results": [asdict(item) for item in self.envelopes],
        }

    def current_tokens(self, stage: str = "working") -> int:
        return estimate_tokens(self.system_prompt) + estimate_tokens(self._base_payload(stage))

    def compact_if_needed(self) -> CompactionEvent | None:
        before = self.current_tokens()
        hard_limit = max(1, self.max_tokens - self.output_reserve_tokens)
        # Misconfigured environment values must never let the soft threshold
        # sit above the model's actual input budget.
        if before <= min(self.soft_tokens, hard_limit):
            return None
        removed = 0
        # Keep the newest result for each tool, plus all evidence/detail reads.
        newest: dict[str, ToolEnvelope] = {}
        protected: list[ToolEnvelope] = []
        for item in self.envelopes:
            if item.tool in {"geo_evidence_get", "geo_metric_series", "geo_internal_message_get"}:
                protected.append(item)
            else:
                newest[item.tool] = item
        kept_ids = {item.call_id for item in [*protected, *newest.values()]}
        compacted: list[ToolEnvelope] = []
        for item in self.envelopes:
            if item.call_id in kept_ids:
                compacted.append(item)
            else:
                removed += 1
        self.envelopes = compacted
        after = self.current_tokens()
        # Detail reads are normally protected because they carry the evidence
        # used in synthesis.  If those previews alone breach the hard input
        # budget, retain their artifact handles and record counts while
        # replacing the oldest full previews with bounded summaries.  The
        # complete payload remains available in qa_artifacts.
        if after >= hard_limit:
            for item in self.envelopes:
                if after < hard_limit:
                    break
                if isinstance(item.preview, dict) and item.preview.get("_preview", {}).get("compacted"):
                    continue
                item.preview = {
                    "_preview": {"compacted": True, "reason": "context_hard_limit"},
                }
                self._stable_previews[item.call_id] = item.preview
                after = self.current_tokens()
        # Envelope metadata itself has a cost.  Once every preview has been
        # reduced to an artifact marker, discard the oldest markers while
        # retaining at least the newest result for the next model turn.
        while after >= hard_limit and len(self.envelopes) > 1:
            self.envelopes.pop(0)
            removed += 1
            after = self.current_tokens()
        self.stop_discovery = after >= hard_limit
        return CompactionEvent(before, after, removed, self.stop_discovery)

    def render_messages(self, *, stage: str) -> list[dict[str, str]]:
        self.compact_if_needed()
        payload = self._base_payload(stage)
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        ]
