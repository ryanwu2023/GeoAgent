"""ResearchGraph 的结构、范围、权限和累计预算校验。"""
from __future__ import annotations

from .qa_plan_schema import QaPolicy, ResearchGraph, TaskStatus, ValidationResult


_INACTIVE = {TaskStatus.CANCELLED}


def _cycle_nodes(graph: ResearchGraph) -> set[str]:
    dependencies = {task.task_id: task.depends_on for task in graph.tasks}
    visiting: set[str] = set()
    visited: set[str] = set()
    cycle: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visited or task_id not in dependencies:
            return
        if task_id in visiting:
            cycle.add(task_id)
            return
        visiting.add(task_id)
        for dependency in dependencies[task_id]:
            if dependency in visiting:
                cycle.update({task_id, dependency})
            else:
                visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for item in dependencies:
        visit(item)
    return cycle


def _critical_path_seconds(graph: ResearchGraph) -> int:
    tasks = {task.task_id: task for task in graph.tasks}
    memo: dict[str, int] = {}

    def duration(task_id: str, trail: set[str]) -> int:
        if task_id in memo:
            return memo[task_id]
        if task_id in trail or task_id not in tasks:
            return 0
        task = tasks[task_id]
        previous = max((duration(item, {*trail, task_id}) for item in task.depends_on), default=0)
        own_seconds = 0 if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED} else task.budget.seconds
        memo[task_id] = previous + own_seconds
        return memo[task_id]

    return max((duration(task_id, set()) for task_id in tasks), default=0)


def validate_graph(graph: ResearchGraph, policy: QaPolicy) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    ids = [task.task_id for task in graph.tasks]
    known = set(ids)
    if len(known) != len(ids):
        errors.append("duplicate_task_id")

    unknown_dependencies = sorted({
        dependency for task in graph.tasks for dependency in task.depends_on if dependency not in known
    })
    if unknown_dependencies:
        errors.append("unknown_dependency:" + ",".join(unknown_dependencies))
    cycle = _cycle_nodes(graph)
    if cycle:
        errors.append("dependency_cycle:" + ",".join(sorted(cycle)))

    allowed_categories = set(policy.allowed_tool_categories)
    allowed_topics = set(graph.scope.topics)
    active = [task for task in graph.tasks if task.status not in _INACTIVE]
    for task in graph.tasks:
        if not task.allowed_tool_categories:
            errors.append(f"task_missing_tool_categories:{task.task_id}")
        if not task.acceptance_criteria:
            errors.append(f"task_missing_acceptance_criteria:{task.task_id}")
        unknown_categories = set(task.allowed_tool_categories) - allowed_categories
        if unknown_categories:
            errors.append(f"unknown_tool_category:{task.task_id}:{','.join(sorted(unknown_categories))}")
        if not set(task.scope_topics).issubset(allowed_topics):
            errors.append(f"topic_out_of_scope:{task.task_id}")
        if "internal_research" in task.allowed_tool_categories and "internal" not in graph.scope.source_permissions:
            errors.append(f"source_permission_denied:{task.task_id}:internal")
        if task.budget.tool_calls > policy.max_tool_calls_per_task:
            errors.append(f"task_tool_budget:{task.task_id}")
        if task.budget.context_tokens > policy.max_context_tokens:
            errors.append(f"task_context_budget:{task.task_id}")

    if len(active) > policy.max_tasks:
        errors.append(f"active_task_limit:{len(active)}>{policy.max_tasks}")
    mode_limits = {"fast": (1, 2), "standard": (2, 4), "research": (4, policy.max_tasks)}
    minimum, maximum = mode_limits[graph.mode]
    if not minimum <= len(active) <= maximum:
        errors.append(f"mode_task_count:{graph.mode}:{len(active)}")
    remaining = [
        task for task in graph.tasks
        if task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
    ]
    planned_calls = sum(task.budget.tool_calls for task in remaining)
    if graph.cumulative_tool_calls + planned_calls > policy.max_total_tool_calls:
        errors.append("cumulative_tool_budget")
    usable_seconds = policy.deadline_seconds - policy.reserve_synthesis_seconds
    if graph.cumulative_seconds + _critical_path_seconds(graph) > usable_seconds:
        errors.append("cumulative_time_budget")
    if graph.revision < 1 or (
        graph.revision == 1 and graph.parent_revision is not None
    ) or (
        graph.revision > 1 and graph.parent_revision != graph.revision - 1
    ):
        errors.append("revision_chain")
    if graph.revision - 1 > policy.max_plan_revisions:
        errors.append("revision_limit")
    if graph.scope_version != graph.scope.version:
        errors.append("scope_version_mismatch")
    if graph.deadline_at != graph.scope.deadline_at:
        errors.append("deadline_mismatch")
    if graph.started_at != graph.scope.run_started_at:
        errors.append("started_at_mismatch")
    roots = [task.task_id for task in graph.tasks if not task.depends_on]
    if graph.tasks and not roots and not cycle:
        errors.append("unreachable_graph")
    if not graph.tasks:
        errors.append("empty_graph")
    if any(task.no_progress_rounds > policy.max_no_progress_rounds for task in graph.tasks):
        warnings.append("task_no_progress_limit_reached")
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings, roots=roots)
