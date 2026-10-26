"""Run-local shared cache for idempotent read-only QA tool calls."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Awaitable, Callable


def canonical_tool_key(scope: dict[str, Any], tool: str, arguments: dict[str, Any]) -> str:
    """Return a stable cache key without exposing argument text in traces."""
    value = {
        "scope": {
            key: scope.get(key, "")
            for key in ("snapshot_id", "topic", "dimension", "start", "end")
        },
        "tool": str(tool),
        "arguments": arguments if isinstance(arguments, dict) else {},
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedToolResult:
    value: Any
    cache_reused: bool


class SharedToolCache:
    """Coalesce concurrent calls and retain only successful results.

    Values are copied both on write and read so callers cannot mutate the
    shared receipt that another specialist will later inspect.
    """

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()

    async def get_or_execute(
        self,
        key: str,
        executor: Callable[[], Awaitable[Any]],
    ) -> CachedToolResult:
        owner = False
        async with self._lock:
            if key in self._values:
                return CachedToolResult(deepcopy(self._values[key]), True)
            pending = self._pending.get(key)
            if pending is None:
                pending = asyncio.get_running_loop().create_future()
                # Avoid an unobserved-future warning when the owner is the only
                # waiter and its execution fails.
                pending.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
                self._pending[key] = pending
                owner = True

        if not owner:
            value = await asyncio.shield(pending)
            return CachedToolResult(deepcopy(value), True)

        try:
            value = await executor()
        except BaseException as exc:
            async with self._lock:
                current = self._pending.pop(key, None)
                if current is not None and not current.done():
                    current.set_exception(exc)
            raise
        else:
            stored = deepcopy(value)
            async with self._lock:
                self._values[key] = stored
                current = self._pending.pop(key, None)
                if current is not None and not current.done():
                    current.set_result(stored)
            return CachedToolResult(deepcopy(stored), False)

    async def clear(self) -> None:
        async with self._lock:
            self._values.clear()

