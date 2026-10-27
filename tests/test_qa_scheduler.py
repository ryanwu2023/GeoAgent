import asyncio
import time

from backend.qa_scheduler import ToolRequest, execute_batch, execute_one


class FakeTools:
    def __init__(self):
        self.active = 0
        self.peak = 0
        self.attempts = {}

    def call(self, name, arguments):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            time.sleep(arguments.get("delay", 0.01))
            self.attempts[name] = self.attempts.get(name, 0) + 1
            if name == "bad":
                raise ValueError("参数错误")
            if name == "flaky" and self.attempts[name] < 2:
                raise RuntimeError("暂时不可用")
            return {"name": name}
        finally:
            self.active -= 1


class TimeoutTools:
    def __init__(self):
        self.calls = 0

    def call(self, name, arguments):
        self.calls += 1
        time.sleep(0.08)
        return {"name": name}


def test_batch_is_bounded_stable_and_keeps_partial_success():
    tools = FakeTools()
    requests = [
        ToolRequest("c1", "slow", {"delay": 0.04}),
        ToolRequest("c2", "bad", {}),
        ToolRequest("c3", "fast", {}),
    ]
    results = asyncio.run(execute_batch(requests, tools, max_concurrency=2))
    assert [item.call_id for item in results] == ["c1", "c2", "c3"]
    assert [item.ok for item in results] == [True, False, True]
    assert results[1].error.kind == "invalid_request"
    assert tools.peak <= 2


def test_retryable_error_is_retried_but_keeps_call_id():
    result = asyncio.run(execute_one(ToolRequest("same", "flaky", {}), FakeTools(), retry_attempts=2))
    assert result.ok is True
    assert result.call_id == "same"
    assert result.attempts == 2


def test_timeout_does_not_start_a_second_uncancellable_thread():
    tools = TimeoutTools()
    result = asyncio.run(execute_one(
        ToolRequest("timeout", "slow", {}, timeout_seconds=0.01),
        tools,
        retry_attempts=2,
    ))
    assert result.ok is False
    assert result.error.kind == "tool_timeout"
    assert result.error.retryable is True
    assert result.attempts == 1
    assert tools.calls == 1


def test_tool_errors_are_redacted_before_persistence_or_progress_output():
    class SecretTools:
        def call(self, name, arguments):
            raise ValueError("Authorization: Bearer top-secret api_key=abc123")

    result = asyncio.run(execute_one(ToolRequest("secret", "bad", {}), SecretTools()))
    assert result.ok is False
    assert result.error is not None
    assert "top-secret" not in result.error.message
    assert "abc123" not in result.error.message
    assert result.error.message == "Authorization=[已脱敏] [已脱敏] api_key=[已脱敏]"


def test_parallel_calls_compete_for_one_atomic_attempt_budget():
    remaining = 1
    lock = asyncio.Lock()

    async def claim():
        nonlocal remaining
        async with lock:
            if remaining <= 0:
                return False
            remaining -= 1
            return True

    results = asyncio.run(execute_batch(
        [ToolRequest("a", "fast", {}), ToolRequest("b", "slow", {})],
        FakeTools(), max_concurrency=2, claim_attempt=claim,
    ))
    assert sum(item.ok for item in results) == 1
    rejected = next(item for item in results if not item.ok)
    assert rejected.error is not None
    assert rejected.error.kind == "budget_exhausted"
    assert rejected.attempts == 0
