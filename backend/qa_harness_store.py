"""SQLite persistence and redaction for local QA harness runs."""
from __future__ import annotations

from dataclasses import asdict
import json
import re
from typing import Any
from uuid import uuid4

from .qa_harness_state import HarnessBudget, HarnessEvent, HarnessRun, HarnessScope
from .store import canonical, db, now


_SECRET_KEY = re.compile(r"(?:authorization|password|access[_-]?token|api[_-]?key|secret)", re.I)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.I)
_LOCAL_PATH_START = re.compile(
    r"(?i)(?:[a-z]:\\|\\\\[^\\/\r\n]+\\|(?<![\w:])/(?:Users|home|var|tmp)/)"
)


def _safe_string(value: str) -> str:
    value = _BEARER.sub("[已脱敏]", value)
    path = _LOCAL_PATH_START.search(value)
    if path is not None:
        # Local paths may legally contain spaces, quotes and punctuation, so
        # there is no reliable delimiter in an arbitrary upstream error or
        # document string. Preserve only the safe prefix and redact the whole
        # remaining suffix instead of risking a partial path disclosure.
        value = value[:path.start()] + "[本地路径已脱敏]"
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): ("[已脱敏]" if _SECRET_KEY.search(str(key)) else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _safe_string(value)
    return value


def redact_trace_value(value: Any) -> Any:
    """Redact data before it crosses persistence, stream or detail boundaries."""
    return _redact(value)


def summarize_arguments(arguments: Any, *, max_text: int = 320, max_items: int = 20) -> dict[str, Any]:
    """Return a compact, JSON-safe and redacted tool argument projection."""
    safe = _redact(arguments)
    if not isinstance(safe, dict):
        return {"value": _safe_string(str(safe))[:max_text]}
    summary: dict[str, Any] = {}
    for index, (key, value) in enumerate(safe.items()):
        if index >= max_items:
            summary["_truncated"] = True
            break
        if isinstance(value, str):
            summary[str(key)] = value if len(value) <= max_text else value[:max_text] + "…"
        elif isinstance(value, list):
            summary[str(key)] = value[:20]
            if len(value) > 20:
                summary[f"_{key}_truncated"] = True
        elif isinstance(value, dict):
            summary[str(key)] = summarize_arguments(value, max_text=max_text, max_items=max_items)
        else:
            summary[str(key)] = value
    return summary


def preview_is_truncated(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("truncated") is True:
            return True
        marker = value.get("_preview")
        if isinstance(marker, dict) and marker.get("truncated") is True:
            return True
        return any(preview_is_truncated(item) for item in value.values())
    if isinstance(value, list):
        return any(preview_is_truncated(item) for item in value)
    return isinstance(value, str) and "预览已截断" in value


class ScopeMismatchError(ValueError):
    pass


class HarnessStore:
    def create_run(
        self, *, run_id: str, answer_id: str, scope: HarnessScope, question: str,
        plan: dict[str, Any], budget: HarnessBudget, model: str,
    ) -> HarnessRun:
        timestamp = now()
        run = HarnessRun(
            run_id=run_id, answer_id=answer_id, question=question, scope=scope,
            plan=plan, budget=budget, model=model, created_at=timestamp, updated_at=timestamp,
        )
        payload = canonical(_redact(run.to_dict()))
        with db() as connection:
            connection.execute(
                "INSERT INTO qa_runs VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, answer_id, scope.snapshot_id, scope.stable_hash(), run.phase.value,
                 run.status, timestamp, timestamp, payload),
            )
        return run

    def append_step(self, run_id: str, event: HarnessEvent) -> None:
        payload = canonical(_redact(asdict(event)))
        with db() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO qa_steps(run_id,sequence,created_at,payload) VALUES (?,?,?,?)",
                (run_id, event.sequence, now(), payload),
            )

    def save_checkpoint(self, run: HarnessRun) -> None:
        run.updated_at = now()
        payload = canonical(_redact(run.to_dict()))
        with db() as connection:
            cursor = connection.execute(
                """UPDATE qa_runs SET answer_id=?,snapshot_id=?,scope_hash=?,phase=?,status=?,
                   updated_at=?,payload=? WHERE run_id=?""",
                (run.answer_id, run.scope.snapshot_id, run.scope.stable_hash(), run.phase.value,
                 run.status, run.updated_at, payload, run.run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("Harness 运行不存在")

    def save_artifact(
        self, run_id: str, call_id: str, payload: Any, preview: Any, token_estimate: int,
    ) -> str:
        safe_payload = _redact(payload)
        artifact_id = f"qa-art-{uuid4().hex}"
        raw = canonical(safe_payload)
        with db() as connection:
            connection.execute(
                "INSERT INTO qa_artifacts VALUES (?,?,?,?,?,?,?,?)",
                (artifact_id, run_id, call_id, now(), len(raw.encode("utf-8")), int(token_estimate),
                 canonical(_redact(preview)), raw),
            )
        return artifact_id

    def load_artifact(self, artifact_id: str) -> Any | None:
        return self.load_artifacts([artifact_id]).get(artifact_id)

    def load_artifacts(self, artifact_ids: list[str]) -> dict[str, Any]:
        """Load a run's cached tool payloads in one consistent DB read."""
        ids = list(dict.fromkeys(str(item) for item in artifact_ids if item))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with db() as connection:
            rows = connection.execute(
                f"SELECT artifact_id,payload FROM qa_artifacts WHERE artifact_id IN ({placeholders})",
                ids,
            ).fetchall()
        return {str(row["artifact_id"]): json.loads(row["payload"]) for row in rows}

    def load_run(self, run_id: str) -> HarnessRun | None:
        with db() as connection:
            row = connection.execute("SELECT payload FROM qa_runs WHERE run_id=?", (run_id,)).fetchone()
        return HarnessRun.from_dict(json.loads(row["payload"])) if row else None

    def find_resumable_run(self, run_id: str, scope: HarnessScope) -> HarnessRun | None:
        run = self.load_run(run_id)
        if run is None:
            return None
        if run.scope.stable_hash() != scope.stable_hash():
            raise ScopeMismatchError("运行范围与当前快照或筛选条件不一致")
        return run

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        with db() as connection:
            rows = connection.execute(
                "SELECT sequence,created_at,payload FROM qa_steps WHERE run_id=? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [{**json.loads(row["payload"]), "created_at": row["created_at"]} for row in rows]

    def get_tool_call_detail(self, run_id: str, call_id: str) -> dict[str, Any] | None:
        """Load one persisted tool call, strictly scoped to its owning run."""
        run = self.load_run(run_id)
        if run is None:
            return None
        state = run.tool_calls.get(call_id)
        if state is None:
            return None
        if state.task_id:
            task = run.agent_tasks.get(state.task_id)
            if task is None or task.agent_id != state.agent_id or task.agent_role != state.agent_role:
                return None
            if task.status in {"completed", "degraded"} and call_id not in task.tool_call_ids:
                return None
        artifact: dict[str, Any] | None = None
        if state.artifact_id:
            with db() as connection:
                row = connection.execute(
                    """SELECT artifact_id,run_id,call_id,created_at,byte_count,token_estimate,
                              preview,payload
                       FROM qa_artifacts
                       WHERE artifact_id=? AND run_id=? AND call_id=?""",
                    (state.artifact_id, run_id, call_id),
                ).fetchone()
            if row is not None:
                artifact = {
                    "artifact_id": str(row["artifact_id"]),
                    "created_at": str(row["created_at"]),
                    "byte_count": int(row["byte_count"]),
                    "token_estimate": int(row["token_estimate"]),
                    "preview": _redact(json.loads(row["preview"])),
                    "payload": _redact(json.loads(row["payload"])),
                }
        arguments = _redact(state.arguments)
        summary = state.arguments_summary or summarize_arguments(arguments)
        return _redact({
            "event_version": "tool-trace/v1",
            "run_id": run.run_id,
            "answer_id": run.answer_id,
            "snapshot_id": run.scope.snapshot_id,
            "scope_hash": run.scope.stable_hash(),
            "phase": state.phase,
            "task_id": state.task_id,
            "agent_id": state.agent_id,
            "agent_role": state.agent_role,
            "parent_agent_id": state.parent_agent_id,
            "status": state.status,
            "mcp_server": state.mcp_server or "geoagent",
            "tool": state.tool,
            "call_id": state.call_id,
            "attempt": max(1, int(state.attempts or 1)),
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "duration_ms": int(state.duration_ms or 0),
            "arguments": arguments,
            "arguments_summary": summary,
            "result_count": state.result_count,
            "result_summary": state.result_summary,
            "artifact_id": state.artifact_id,
            "truncated": bool(state.truncated or (artifact and preview_is_truncated(artifact["preview"]))),
            "error": state.error,
            "evidence_ids": list(state.evidence_ids),
            "artifact": artifact,
        })

    def complete_run(self, run_id: str, status: str, answer_id: str) -> None:
        run = self.load_run(run_id)
        if run is None:
            raise KeyError("Harness 运行不存在")
        run.status = status
        run.answer_id = answer_id
        self.save_checkpoint(run)


_default_store = HarnessStore()


def create_run(**kwargs) -> HarnessRun:
    return _default_store.create_run(**kwargs)


def append_step(run_id: str, event: HarnessEvent) -> None:
    _default_store.append_step(run_id, event)


def save_checkpoint(run: HarnessRun) -> None:
    _default_store.save_checkpoint(run)


def save_artifact(run_id: str, call_id: str, payload: Any, preview: Any, token_estimate: int) -> str:
    return _default_store.save_artifact(run_id, call_id, payload, preview, token_estimate)


def load_run(run_id: str) -> HarnessRun | None:
    return _default_store.load_run(run_id)


def find_resumable_run(run_id: str, scope: HarnessScope) -> HarnessRun | None:
    return _default_store.find_resumable_run(run_id, scope)


def complete_run(run_id: str, status: str, answer_id: str) -> None:
    _default_store.complete_run(run_id, status, answer_id)
