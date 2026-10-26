"""Serializable state for the local, read-only QA harness."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any


class HarnessPhase(str, Enum):
    PLANNING = "planning"
    DISCOVERING = "discovering"
    READING = "reading"
    REVIEWING = "reviewing"
    SYNTHESIZING = "synthesizing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    DEGRADED = "degraded"


_ALLOWED_TRANSITIONS = {
    HarnessPhase.PLANNING: {HarnessPhase.DISCOVERING, HarnessPhase.DEGRADED},
    HarnessPhase.DISCOVERING: {HarnessPhase.READING, HarnessPhase.DEGRADED},
    HarnessPhase.READING: {HarnessPhase.REVIEWING, HarnessPhase.DEGRADED},
    HarnessPhase.REVIEWING: {HarnessPhase.SYNTHESIZING, HarnessPhase.DEGRADED},
    HarnessPhase.SYNTHESIZING: {HarnessPhase.VALIDATING, HarnessPhase.DEGRADED},
    HarnessPhase.VALIDATING: {HarnessPhase.COMPLETED, HarnessPhase.DEGRADED},
    HarnessPhase.COMPLETED: set(),
    HarnessPhase.DEGRADED: set(),
}


class InvalidHarnessTransition(ValueError):
    pass


@dataclass(frozen=True)
class HarnessScope:
    snapshot_id: str
    topic: str = ""
    dimension: str = ""
    start: str = ""
    end: str = ""

    def stable_hash(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class HarnessBudget:
    max_tool_calls: int = 18
    max_total_tool_attempts: int = 36
    max_retries: int = 2
    max_context_tokens: int = 32000
    soft_context_tokens: int = 24000
    output_reserve_tokens: int = 6000


@dataclass
class ToolCallState:
    call_id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    attempts: int = 0
    artifact_id: str | None = None
    result_count: int | None = None
    duration_ms: int = 0
    error: dict[str, Any] | None = None
    phase: str = ""
    mcp_server: str = "geoagent"
    started_at: str = ""
    finished_at: str = ""
    arguments_summary: dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    truncated: bool = False
    evidence_ids: list[str] = field(default_factory=list)
    task_id: str = ""
    agent_id: str = "coordinator-agent"
    agent_role: str = "coordinator"
    parent_agent_id: str = ""


@dataclass
class AgentTaskState:
    task_id: str
    agent_id: str
    agent_role: str
    objective: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    status: str = "pending"
    started_at: str = ""
    finished_at: str = ""
    tool_call_ids: list[str] = field(default_factory=list)
    memo: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


@dataclass
class HarnessEvent:
    sequence: int
    phase: str
    kind: str
    status: str
    title: str = ""
    summary: str = ""
    details: list[str] = field(default_factory=list)
    call_id: str = ""
    tool: str = ""
    duration_ms: int = 0
    result_count: int | None = None
    retry_count: int = 0
    context_tokens: int | None = None
    compacted: bool = False
    recovered: bool = False
    evidence_ids: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    event_version: str = "tool-trace/v1"
    run_id: str = ""
    mcp_server: str = "geoagent"
    attempt: int = 1
    started_at: str = ""
    finished_at: str = ""
    arguments_summary: dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    artifact_id: str = ""
    truncated: bool = False
    cache_reused: bool = False
    matched_total: int | None = None
    matched_already_seen: int | None = None
    returned_new: int | None = None
    coverage: list[dict[str, Any]] = field(default_factory=list)
    readiness: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    task_id: str = ""
    agent_id: str = "coordinator-agent"
    agent_role: str = "coordinator"
    parent_agent_id: str = ""


@dataclass
class HarnessRun:
    run_id: str
    answer_id: str
    question: str
    scope: HarnessScope
    phase: HarnessPhase = HarnessPhase.PLANNING
    status: str = "running"
    plan: dict[str, Any] = field(default_factory=dict)
    budget: HarnessBudget = field(default_factory=HarnessBudget)
    tool_calls: dict[str, ToolCallState] = field(default_factory=dict)
    agent_tasks: dict[str, AgentTaskState] = field(default_factory=dict)
    tool_attempts_used: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    full_text_ids: list[str] = field(default_factory=list)
    internal_message_ids: list[str] = field(default_factory=list)
    internal_full_text_ids: list[str] = field(default_factory=list)
    research_memory: dict[str, Any] = field(default_factory=dict)
    last_valid_draft: str = ""
    model: str = ""
    errors: list[dict[str, Any]] = field(default_factory=list)
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["phase"] = self.phase.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HarnessRun":
        data = dict(value)
        data["scope"] = HarnessScope(**data.get("scope", {}))
        data["budget"] = HarnessBudget(**data.get("budget", {}))
        data["phase"] = HarnessPhase(data.get("phase", HarnessPhase.PLANNING.value))
        data["tool_calls"] = {
            key: ToolCallState(**item) for key, item in (data.get("tool_calls") or {}).items()
        }
        data["agent_tasks"] = {
            key: AgentTaskState(**item) for key, item in (data.get("agent_tasks") or {}).items()
        }
        research_memory = data.get("research_memory") if isinstance(data.get("research_memory"), dict) else {}
        research_memory.setdefault("ledger", {"version": 1, "entries": []})
        data["research_memory"] = research_memory
        return cls(**data)


def transition(run: HarnessRun, next_phase: HarnessPhase) -> HarnessRun:
    if next_phase == run.phase:
        return run
    if next_phase not in _ALLOWED_TRANSITIONS[run.phase]:
        raise InvalidHarnessTransition(f"非法 Harness 状态迁移：{run.phase.value} -> {next_phase.value}")
    run.phase = next_phase
    if next_phase == HarnessPhase.COMPLETED:
        run.status = "completed"
    elif next_phase == HarnessPhase.DEGRADED:
        run.status = "degraded"
    return run
