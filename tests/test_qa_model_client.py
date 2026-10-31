import asyncio
import time

import pytest

from backend.qa_cost import QaCostLedger
from backend.qa_model_client import ModelCallContext, QaModelClient, ToolProtocolUnsupported
from backend.qa_role_models import RoleModelResolver


def resolver():
    return RoleModelResolver(default_models=[{
        "provider": "openai-compatible", "base_url": "https://model.example/v1",
        "model": "reasoner", "key_ref": "backend_default", "supports_tools": True,
        "context_tokens": 64000, "data_classifications": ["public", "internal"],
        "input_price_per_million": 1, "output_price_per_million": 2,
        "price_version": "v1",
    }])


def context(**kwargs):
    return ModelCallContext(
        deadline=time.monotonic() + 30,
        ledger=QaCostLedger(max_cost_cny=1), config_version="cfg-1", **kwargs,
    )


def test_native_tool_calls_and_real_usage_are_preserved_without_text():
    async def transport(model, payload, timeout):
        assert payload["tools"] and payload["tool_choice"] == "auto"
        return {
            "id": "req-1", "model": model.model, "usage": {"prompt_tokens": 20, "completion_tokens": 8},
            "choices": [{"finish_reason": "tool_calls", "message": {
                "content": None,
                "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "search", "arguments": "{}"}}],
            }}],
        }

    reply = asyncio.run(QaModelClient(resolver(), transport).complete(
        [{"role": "user", "content": "检索"}], role="planner",
        tools=[{"type": "function", "function": {"name": "search"}}], context=context(),
    ))
    assert reply.text == ""
    assert reply.finish_reason == "tool_calls"
    assert reply.tool_calls[0]["id"] == "call-1"
    assert reply.usage == {"input_tokens": 20, "output_tokens": 8, "estimated": False}


def test_missing_usage_is_marked_estimated_and_truncation_is_visible():
    async def transport(model, payload, timeout):
        return {"choices": [{"finish_reason": "length", "message": {"content": "部分回答"}}]}

    reply = asyncio.run(QaModelClient(resolver(), transport).complete(
        [{"role": "user", "content": "回答"}], role="worker_fast", tools=None,
        context=context(max_output_tokens=123),
    ))
    assert reply.finish_reason == "length"
    assert reply.usage["estimated"] is True
    assert reply.usage["output_tokens"] == 123


def test_only_explicit_tool_protocol_error_can_use_json_compatibility():
    calls = []

    async def transport(model, payload, timeout):
        calls.append(payload)
        if "tools" in payload:
            raise ToolProtocolUnsupported("tools unsupported")
        return {"choices": [{"finish_reason": "stop", "message": {"content": "{\"action\":\"final\"}"}}]}

    reply = asyncio.run(QaModelClient(resolver(), transport).complete(
        [{"role": "user", "content": "回答"}], role="planner",
        tools=[{"type": "function", "function": {"name": "search"}}],
        context=context(json_action_compat=True),
    ))
    assert len(calls) == 2
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert reply.text.startswith("{")


def test_auth_or_quota_error_is_not_misclassified_as_protocol_fallback():
    calls = 0

    async def transport(model, payload, timeout):
        nonlocal calls
        calls += 1
        raise PermissionError("401")

    with pytest.raises(PermissionError):
        asyncio.run(QaModelClient(resolver(), transport).complete(
            [{"role": "user", "content": "回答"}], role="planner",
            tools=[{"type": "function", "function": {"name": "search"}}],
            context=context(json_action_compat=True),
        ))
    assert calls == 1
