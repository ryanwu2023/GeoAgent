"""依据具体证据缺口修订研究图，不使用泛化的重复搜索。"""
from __future__ import annotations

from .qa_plan_schema import (
    ProgressFingerprint, QaPolicy, ResearchGraph, ResearchTask, TaskBudget,
    TaskEvaluation, TaskStatus,
)


_ACTION_POLICY = {
    "source_verification": ("source_verification", ["public_evidence"], "核对原始来源及独立确认关系"),
    "actual_action_verification": ("actual_action_verification", ["public_evidence"], "核验表态是否已有实际行动证据"),
    "full_text_validation": ("full_text_validation", ["public_evidence"], "读取关键材料全文并绑定原文位置"),
    "counterevidence": ("counterevidence", ["public_evidence", "analysis"], "补查否认、反证和失效条件"),
    "metric_verification": ("metric_verification", ["market_data"], "补齐指标日期、单位和统计口径"),
    "metric_conflict_verification": (
        "metric_conflict_verification", ["market_data", "public_evidence", "analysis"],
        "核验指标与叙事冲突的时点和口径",
    ),
}


def _eligible(evaluations: list[TaskEvaluation]) -> list[TaskEvaluation]:
    return [
        item for item in evaluations
        if item.result in {"incomplete", "conflicted"}
        and item.suggested_action in _ACTION_POLICY
    ]


def replan(
    graph: ResearchGraph,
    evaluations: list[TaskEvaluation],
    progress: ProgressFingerprint,
    policy: QaPolicy,
) -> ResearchGraph | None:
    candidates = _eligible(list(evaluations))
    if not candidates or graph.revision >= 1 + policy.max_plan_revisions:
        return None

    progress_key = progress.stable_key()
    repeated = bool(graph.progress_fingerprint_keys and graph.progress_fingerprint_keys[-1] == progress_key)
    no_progress_rounds = graph.no_progress_rounds + 1 if repeated else 0
    if repeated and no_progress_rounds >= policy.max_no_progress_rounds:
        return None

    updated = ResearchGraph.from_dict(graph.to_dict())
    updated.parent_revision = graph.revision
    updated.revision = graph.revision + 1
    updated.no_progress_rounds = no_progress_rounds
    updated.progress_fingerprint_keys = [*graph.progress_fingerprint_keys, progress_key]
    updated.added_task_ids = []
    updated.cancelled_task_ids = []
    updated.replaced_task_ids = []
    reasons: list[str] = []
    existing_types = {
        task.task_type for task in updated.tasks
        if task.status not in {TaskStatus.CANCELLED, TaskStatus.COMPLETED}
    }

    for evaluation in candidates:
        task_type, categories, label = _ACTION_POLICY[evaluation.suggested_action]
        if task_type in existing_types or len(updated.tasks) >= policy.max_tasks:
            continue
        parent = next((task for task in updated.tasks if task.task_id == evaluation.task_id), None)
        gaps = evaluation.gaps or evaluation.conflicts or [label]
        detail = "；".join(str(item) for item in gaps[:3])
        task_id = f"R{updated.revision}-{len(updated.added_task_ids) + 1}"
        added = ResearchTask(
            task_id=task_id,
            task_type=task_type,
            objective=f"{label}：{detail}",
            depends_on=[evaluation.task_id] if parent is not None else [],
            allowed_tool_categories=categories,
            acceptance_criteria=[
                "新增信息必须绑定证据ID、原文位置和内容版本",
                "未检得时记录实际查询范围，不得写成事实不存在",
            ],
            budget=TaskBudget(
                tool_calls=min(3, policy.max_tool_calls_per_task),
                seconds=min(35, policy.deadline_seconds - policy.reserve_synthesis_seconds),
                context_tokens=min(12000, policy.max_context_tokens),
            ),
            scope_topics=list(graph.scope.topics),
            dimension=graph.scope.dimension,
            revision_created=updated.revision,
        )
        updated.tasks.append(added)
        updated.added_task_ids.append(task_id)
        existing_types.add(task_type)
        reasons.append(detail)

    if not updated.added_task_ids:
        return None
    updated.revision_reason = "补查具体缺口：" + "；".join(dict.fromkeys(reasons))
    return updated
