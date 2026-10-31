"""图式问答统一模型客户端：原生工具调用、用量结算与明确兼容回退。"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any, Awaitable, Callable

from .qa_cost import QaCostLedger
from .qa_role_models import ModelRequirements, ResolvedModel, RoleModelResolver


class ToolProtocolUnsupported(RuntimeError):
    """网关明确返回不支持原生工具协议。"""


@dataclass(frozen=True)
class ModelReply:
    text: str
    tool_calls: list[dict[str, Any]]
    usage: dict[str, Any]
    model: str
    finish_reason: str
    request_id: str


@dataclass
class ModelCallContext:
    deadline: float
    ledger: QaCostLedger
    config_version: str
    data_classification: str = "public"
    max_output_tokens: int = 4000
    timeout_seconds: float = 60.0
    temperature: float | None = None
    reasoning: bool | None = None
    json_action_compat: bool = False


Transport = Callable[[ResolvedModel, dict[str, Any], float], Awaitable[dict[str, Any]]]


def _token_estimate(value: Any) -> int:
    return max(1, len(json.dumps(value, ensure_ascii=False)) // 4)


class QaModelClient:
    def __init__(self, resolver: RoleModelResolver, transport: Transport) -> None:
        self.resolver = resolver
        self.transport = transport

    async def complete(
        self, messages: list[dict[str, Any]], *, role: str,
        tools: list[dict[str, Any]] | None, context: ModelCallContext,
    ) -> ModelReply:
        remaining = context.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("模型调用开始前已达到运行截止时间")
        resolved = self.resolver.resolve(
            role,
            ModelRequirements(
                supports_tools=bool(tools),
                data_classification=context.data_classification,
                reasoning_preferred=bool(context.reasoning),
            ),
            context.ledger,
        )
        input_estimate = _token_estimate(messages)
        reservation = await context.ledger.reserve(
            role=role, model=resolved.model, price_version=resolved.price_version,
            input_tokens=input_estimate, output_tokens=context.max_output_tokens,
        )
        payload: dict[str, Any] = {
            "model": resolved.model, "messages": messages, "stream": False,
            "max_tokens": context.max_output_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if context.temperature is not None:
            payload["temperature"] = context.temperature
        if context.reasoning is not None:
            payload["reasoning"] = {"enabled": context.reasoning}
        timeout = min(float(context.timeout_seconds), max(0.001, remaining))
        try:
            try:
                response = await self.transport(resolved, payload, timeout)
            except ToolProtocolUnsupported:
                if not tools or not context.json_action_compat:
                    raise
                compatibility_payload = dict(payload)
                compatibility_payload.pop("tools", None)
                compatibility_payload.pop("tool_choice", None)
                compatibility_payload["response_format"] = {"type": "json_object"}
                response = await self.transport(resolved, compatibility_payload, timeout)
            reply = self._parse(response, resolved.model, input_estimate, context.max_output_tokens)
            await context.ledger.settle(
                reservation,
                input_tokens=int(reply.usage["input_tokens"]),
                output_tokens=int(reply.usage["output_tokens"]),
            )
            return reply
        except Exception:
            # 已发送后是否计费无法可靠判断，因此保留预留，等待审计或服务商用量回执。
            await context.ledger.settle(reservation, output_tokens=None)
            raise

    @staticmethod
    def _parse(
        response: dict[str, Any], model: str, input_estimate: int, output_estimate: int,
    ) -> ModelReply:
        choices = response.get("choices") if isinstance(response, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("模型响应缺少 choices")
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        text = message.get("content") if isinstance(message.get("content"), str) else ""
        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        if not text.strip() and not tool_calls:
            raise ValueError("模型未返回正文或工具调用")
        raw_usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        input_tokens = raw_usage.get("prompt_tokens", raw_usage.get("input_tokens"))
        output_tokens = raw_usage.get("completion_tokens", raw_usage.get("output_tokens"))
        estimated = input_tokens is None or output_tokens is None
        usage = {
            "input_tokens": int(input_tokens if input_tokens is not None else input_estimate),
            "output_tokens": int(output_tokens if output_tokens is not None else output_estimate),
            "estimated": estimated,
        }
        return ModelReply(
            text=text, tool_calls=[dict(item) for item in tool_calls if isinstance(item, dict)],
            usage=usage, model=str(response.get("model") or model),
            finish_reason=str(choice.get("finish_reason") or ""),
            request_id=str(response.get("id") or ""),
        )
