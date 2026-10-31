"""图式研究问答的任务画像、任务图与策略公共类型。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Iterable

from .qa_harness_state import HarnessScope
from .qa_scope import ResearchScope


class ExecutionMode(str, Enum):
    FAST = "fast"
    STANDARD = "standard"
    RESEARCH = "research"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    CONFLICTED = "conflicted"
    BLOCKED = "blocked"
    DEGRADED = "degraded"
    CANCELLED = "cancelled"


INTENTS = {
    "fact_lookup", "material_summary", "causal_analysis", "comparison_analysis",
    "comprehensive_research", "current_situation", "scenario_analysis", "prediction",
    "asset_implication", "source_verification", "report_generation",
}
BREADTHS = {"narrow", "single_domain", "multi_domain", "cross_topic"}
DEPTHS = {"shallow", "normal", "deep"}
FRESHNESS_LEVELS = {"historical", "current", "latest"}
RISK_LEVELS = {"low", "medium", "high"}
TOOL_CATEGORIES = {
    "workspace", "public_evidence", "internal_research", "market_data", "analysis",
}
EVALUATION_RESULTS = {"satisfied", "incomplete", "conflicted", "blocked", "degraded"}


def _unique(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


@dataclass
class QaPolicy:
    default_mode: str = "auto"
    quality_profile: str = "quality_first"
    allow_force_research: bool = True
    deadline_seconds: int = 300
    reserve_synthesis_seconds: int = 40
    max_plan_revisions: int = 3
    max_no_progress_rounds: int = 2
    max_tasks: int = 8
    max_parallel_tasks: int = 3
    max_total_tool_calls: int = 48
    max_tool_calls_per_task: int = 8
    max_context_tokens: int = 64000
    max_cost_cny: float | None = None
    synthesis_reserve_output_tokens: int = 6000
    shadow_enabled: bool = False
    shadow_max_cost_cny: float | None = None
    require_counterevidence_for: tuple[str, ...] = (
        "scenario_analysis", "prediction", "asset_implication",
    )
    minimum_mode: dict[str, str] = field(default_factory=lambda: {
        "prediction": "research",
        "scenario_analysis": "research",
        "current_situation": "research",
        "asset_implication": "research",
    })
    allowed_tool_categories: tuple[str, ...] = tuple(sorted(TOOL_CATEGORIES))

    def __post_init__(self) -> None:
        if self.default_mode not in {"auto", "standard", "research"}:
            raise ValueError("invalid_default_mode")
        if self.quality_profile != "quality_first":
            raise ValueError("invalid_quality_profile")
        if self.deadline_seconds <= 0:
            raise ValueError("deadline_seconds_out_of_range")
        if not 0 < self.reserve_synthesis_seconds < self.deadline_seconds:
            raise ValueError("reserve_synthesis_seconds_out_of_range")
        if self.max_plan_revisions < 0 or self.max_no_progress_rounds < 1:
            raise ValueError("invalid_revision_policy")
        if self.max_tasks < 1:
            raise ValueError("max_tasks_out_of_range")
        if not 1 <= self.max_parallel_tasks <= self.max_tasks:
            raise ValueError("max_parallel_tasks_out_of_range")
        if self.max_total_tool_calls < 1 or not 1 <= self.max_tool_calls_per_task <= self.max_total_tool_calls:
            raise ValueError("invalid_tool_limits")
        if self.max_context_tokens < 1024 or self.synthesis_reserve_output_tokens < 1:
            raise ValueError("invalid_context_limits")
        if self.max_cost_cny is not None and self.max_cost_cny <= 0:
            raise ValueError("invalid_max_cost")
        if self.shadow_max_cost_cny is not None and self.shadow_max_cost_cny <= 0:
            raise ValueError("invalid_shadow_max_cost")
        unknown = set(self.allowed_tool_categories) - TOOL_CATEGORIES
        if unknown:
            raise ValueError(f"unknown_policy_tool_categories:{sorted(unknown)}")
        for mode in self.minimum_mode.values():
            if mode not in {item.value for item in ExecutionMode}:
                raise ValueError("invalid_minimum_mode")


@dataclass
class TaskProfile:
    intent: str
    breadth: str
    depth: str
    freshness: str
    risk_level: str
    suggested_mode: str
    confidence: float
    requires_counterevidence: bool = False
    requires_metrics: bool = False
    requires_internal_sources: bool = False
    reason: str = ""
    basis: str = "deterministic"
    forced_research: bool = False

    def __post_init__(self) -> None:
        if self.intent not in INTENTS:
            raise ValueError(f"unknown_intent:{self.intent}")
        if self.breadth not in BREADTHS:
            raise ValueError(f"unknown_breadth:{self.breadth}")
        if self.depth not in DEPTHS:
            raise ValueError(f"unknown_depth:{self.depth}")
        if self.freshness not in FRESHNESS_LEVELS:
            raise ValueError(f"unknown_freshness:{self.freshness}")
        if self.risk_level not in RISK_LEVELS:
            raise ValueError(f"unknown_risk_level:{self.risk_level}")
        if self.suggested_mode not in {item.value for item in ExecutionMode}:
            raise ValueError("invalid_suggested_mode")
        self.confidence = float(self.confidence)
        if not 0 <= self.confidence <= 1:
            raise ValueError("invalid_confidence")


@dataclass
class TaskBudget:
    tool_calls: int = 4
    seconds: int = 45
    context_tokens: int = 12000

    def __post_init__(self) -> None:
        self.tool_calls = int(self.tool_calls)
        self.seconds = int(self.seconds)
        self.context_tokens = int(self.context_tokens)
        if self.tool_calls < 1 or self.seconds < 1 or self.context_tokens < 1:
            raise ValueError("invalid_task_budget")


@dataclass
class ResearchTask:
    task_id: str
    task_type: str
    objective: str
    depends_on: list[str] = field(default_factory=list)
    allowed_tool_categories: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    budget: TaskBudget = field(default_factory=TaskBudget)
    status: TaskStatus = TaskStatus.PENDING
    scope_topics: list[str] = field(default_factory=list)
    dimension: str = ""
    revision_created: int = 1
    attempt_count: int = 0
    no_progress_rounds: int = 0
    claim_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    gap_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.task_id = str(self.task_id).strip()
        self.task_type = str(self.task_type).strip()
        self.objective = str(self.objective).strip()
        if not self.task_id or not self.task_type or not self.objective:
            raise ValueError("task_missing_identity")
        self.depends_on = _unique(self.depends_on)
        self.allowed_tool_categories = _unique(self.allowed_tool_categories)
        self.acceptance_criteria = _unique(self.acceptance_criteria)
        self.scope_topics = _unique(self.scope_topics)
        self.claim_ids = _unique(self.claim_ids)
        self.evidence_ids = _unique(self.evidence_ids)
        self.gap_ids = _unique(self.gap_ids)
        if not isinstance(self.budget, TaskBudget):
            self.budget = TaskBudget(**dict(self.budget))
        if not isinstance(self.status, TaskStatus):
            self.status = TaskStatus(str(self.status))
        self.revision_created = int(self.revision_created)
        self.attempt_count = int(self.attempt_count)
        self.no_progress_rounds = int(self.no_progress_rounds)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ResearchTask":
        data = dict(value)
        data["budget"] = TaskBudget(**dict(data.get("budget") or {}))
        data["status"] = TaskStatus(str(data.get("status") or TaskStatus.PENDING.value))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


def _scope_from_dict(value: dict[str, Any]) -> ResearchScope:
    data = dict(value)
    data.pop("scope_hash", None)
    data["topics"] = tuple(data.get("topics") or [])
    data["source_permissions"] = tuple(data.get("source_permissions") or [])
    data["view_scope"] = HarnessScope(**dict(data.get("view_scope") or {}))
    return ResearchScope(**data)


@dataclass
class ResearchGraph:
    plan_id: str
    revision: int
    parent_revision: int | None
    question: str
    research_objective: str
    mode: str
    scope: ResearchScope
    scope_version: str
    started_at: str
    deadline_at: str
    tasks: list[ResearchTask]
    config_version: str = "default"
    model_role_version: str = "default"
    revision_reason: str = "initial_plan"
    added_task_ids: list[str] = field(default_factory=list)
    cancelled_task_ids: list[str] = field(default_factory=list)
    replaced_task_ids: list[str] = field(default_factory=list)
    cumulative_tool_calls: int = 0
    cumulative_seconds: int = 0
    cumulative_cost_cny: float = 0.0
    no_progress_rounds: int = 0
    claim_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    stage_answer_refs: list[str] = field(default_factory=list)
    progress_fingerprint_keys: list[str] = field(default_factory=list)
    basis: str = "deterministic"

    def __post_init__(self) -> None:
        self.revision = int(self.revision)
        self.parent_revision = int(self.parent_revision) if self.parent_revision is not None else None
        self.mode = self.mode.value if isinstance(self.mode, ExecutionMode) else str(self.mode)
        if self.mode not in {item.value for item in ExecutionMode}:
            raise ValueError("invalid_graph_mode")
        self.tasks = [item if isinstance(item, ResearchTask) else ResearchTask.from_dict(item) for item in self.tasks]
        self.added_task_ids = _unique(self.added_task_ids)
        self.cancelled_task_ids = _unique(self.cancelled_task_ids)
        self.replaced_task_ids = _unique(self.replaced_task_ids)
        self.claim_ids = _unique(self.claim_ids)
        self.evidence_ids = _unique(self.evidence_ids)
        self.gaps = _unique(self.gaps)
        self.stage_answer_refs = _unique(self.stage_answer_refs)
        self.progress_fingerprint_keys = _unique(self.progress_fingerprint_keys)
        self.cumulative_tool_calls = int(self.cumulative_tool_calls)
        self.cumulative_seconds = int(self.cumulative_seconds)
        self.cumulative_cost_cny = float(self.cumulative_cost_cny)
        self.no_progress_rounds = int(self.no_progress_rounds)
        if (
            self.cumulative_tool_calls < 0
            or self.cumulative_seconds < 0
            or self.cumulative_cost_cny < 0
            or self.no_progress_rounds < 0
        ):
            raise ValueError("invalid_graph_cumulative_state")

    @classmethod
    def create(
        cls, *, question: str, research_objective: str, mode: str | ExecutionMode,
        scope: ResearchScope, tasks: Iterable[ResearchTask], revision: int = 1,
        parent_revision: int | None = None, config_version: str = "default",
        model_role_version: str = "default", **kwargs: Any,
    ) -> "ResearchGraph":
        normalized_mode = mode.value if isinstance(mode, ExecutionMode) else str(mode)
        plan_raw = json.dumps({
            "question": question, "scope": scope.stable_hash(), "started_at": scope.run_started_at,
        }, ensure_ascii=False, sort_keys=True)
        plan_id = "plan-" + hashlib.sha256(plan_raw.encode("utf-8")).hexdigest()[:20]
        task_values = list(tasks)
        kwargs.setdefault("added_task_ids", [task.task_id for task in task_values])
        return cls(
            plan_id=plan_id, revision=revision, parent_revision=parent_revision,
            question=question, research_objective=research_objective, mode=normalized_mode,
            scope=scope, scope_version=scope.version, started_at=scope.run_started_at,
            deadline_at=scope.deadline_at, tasks=task_values,
            config_version=config_version, model_role_version=model_role_version,
            **kwargs,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ResearchGraph":
        data = dict(value)
        data["scope"] = _scope_from_dict(dict(data.get("scope") or {}))
        data["tasks"] = [ResearchTask.from_dict(item) for item in data.get("tasks") or []]
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["scope"] = self.scope.to_dict()
        value["tasks"] = [task.to_dict() for task in self.tasks]
        return value


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)


@dataclass
class TaskEvaluation:
    task_id: str
    criterion_id: str
    result: str
    claim_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    span_refs: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    suggested_action: str = ""

    def __post_init__(self) -> None:
        if self.result not in EVALUATION_RESULTS:
            raise ValueError("invalid_task_evaluation_result")


@dataclass
class ProgressFingerprint:
    independent_origin_groups: list[str] = field(default_factory=list)
    full_text_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    resolved_conflict_ids: list[str] = field(default_factory=list)
    conclusion_boundaries: list[str] = field(default_factory=list)

    def stable_key(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "progress-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
