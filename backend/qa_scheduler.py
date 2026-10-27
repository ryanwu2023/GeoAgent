"""Bounded execution for the existing read-only GeoAgent tools."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import time
from typing import Any, Awaitable, Callable, Sequence

from .qa_tool_cache import SharedToolCache, canonical_tool_key


@dataclass(frozen=True)
class ToolRequest:
    call_id: str
    tool: str
    arguments: dict[str, Any]
    timeout_seconds: float = 30.0


@dataclass
class HarnessError:
    kind: str
    message: str
    retryable: bool


@dataclass
class ToolExecutionResult:
    call_id: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    result: Any = None
    error: HarnessError | None = None
    attempts: int = 1
    duration_ms: int = 0
    cache_reused: bool = False


_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.I)
_CREDENTIAL = re.compile(
    r"\b(api[_-]?key|access[_-]?token|password|secret|authorization)\b\s*[:=]\s*([^\s,;]+)",
    re.I,
)


def _safe_error_message(exc: BaseException) -> str:
    message = str(exc) or exc.__class__.__name__
    message = _BEARER.sub("Bearer [已脱敏]", message)
    return _CREDENTIAL.sub(lambda match: f"{match.group(1)}=[已脱敏]", message)


def _classify_error(exc: BaseException) -> HarnessError:
    if isinstance(exc, TimeoutError) or isinstance(exc, asyncio.TimeoutError):
        return HarnessError("tool_timeout", "工具调用超时", True)
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return HarnessError("invalid_request", _safe_error_message(exc).strip("'"), False)
    return HarnessError("tool_unavailable", _safe_error_message(exc), True)


async def execute_one(
    request: ToolRequest,
    tools: Any,
    *,
    retry_attempts: int = 2,
    claim_attempt: Callable[[], Awaitable[bool]] | None = None,
    shared_cache: SharedToolCache | None = None,
    cache_scope: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    started = time.perf_counter()
    attempts = 0
    last_error: HarnessError | None = None
    total_attempts = max(1, retry_attempts + 1)
    while attempts < total_attempts:
        attempts += 1
        try:
            async def invoke():
                if claim_attempt is not None and not await claim_attempt():
                    raise _BudgetExhausted()
                return await asyncio.to_thread(tools.call, request.tool, request.arguments)

            async with asyncio.timeout(request.timeout_seconds):
                if shared_cache is not None and cache_scope is not None:
                    cached = await shared_cache.get_or_execute(
                        canonical_tool_key(cache_scope, request.tool, request.arguments),
                        invoke,
                    )
                    result = cached.value
                    cache_reused = cached.cache_reused
                    if cache_reused and isinstance(result, dict):
                        result["cache_reused"] = True
                        if request.tool == "geo_evidence_search":
                            records = result.get("records") if isinstance(result.get("records"), list) else []
                            result["returned_new"] = 0
                            result["matched_already_seen"] = int(
                                result.get("matched_total") or len(records)
                            )
                            result["top_existing_ids"] = [
                                str(item.get("id")) for item in records
                                if isinstance(item, dict) and item.get("id")
                            ][:5]
                else:
                    result = await invoke()
                    cache_reused = False
            return ToolExecutionResult(
                request.call_id, request.tool, request.arguments, True, result=result,
                attempts=attempts, duration_ms=round((time.perf_counter() - started) * 1000),
                cache_reused=cache_reused,
            )
        except BaseException as exc:  # converted to a tool result by design
            if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            if isinstance(exc, _BudgetExhausted):
                attempts = max(0, attempts - 1)
                last_error = HarnessError("budget_exhausted", "运行级工具调用预算已用尽", False)
            else:
                last_error = _classify_error(exc)
            # asyncio cannot stop a synchronous function already running in a
            # worker thread.  Retrying a timed-out call here would start a
            # second copy while the first may still be mutating the shared
            # read-only tool session.  Preserve retryable=True so a later
            # harness turn can retry after the worker has naturally exited.
            if last_error.kind == "tool_timeout":
                break
            if not last_error.retryable or attempts >= total_attempts:
                break
    return ToolExecutionResult(
        request.call_id, request.tool, request.arguments, False, error=last_error,
        attempts=attempts, duration_ms=round((time.perf_counter() - started) * 1000),
    )


async def execute_batch(
    requests: Sequence[ToolRequest], tools: Any, *, max_concurrency: int = 4,
    retry_attempts: int = 2,
    claim_attempt: Callable[[], Awaitable[bool]] | None = None,
    shared_cache: SharedToolCache | None = None,
    cache_scope: dict[str, Any] | None = None,
) -> list[ToolExecutionResult]:
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def bounded(index: int, request: ToolRequest):
        async with semaphore:
            return index, await execute_one(
                request, tools, retry_attempts=retry_attempts, claim_attempt=claim_attempt,
                shared_cache=shared_cache, cache_scope=cache_scope,
            )

    completed = await asyncio.gather(*(bounded(index, request) for index, request in enumerate(requests)))
    return [result for _, result in sorted(completed, key=lambda item: item[0])]


class _BudgetExhausted(RuntimeError):
    pass
