"""按角色、能力、数据权限和费用约束解析模型回退链。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .qa_cost import QaCostLedger


@dataclass(frozen=True)
class ModelRequirements:
    supports_tools: bool = False
    min_context_tokens: int = 0
    data_classification: str = "public"
    reasoning_preferred: bool = False


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    base_url: str
    model: str
    key_ref: str
    supports_tools: bool
    context_tokens: int
    data_classifications: tuple[str, ...]
    reasoning: bool
    input_price_per_million: float | None
    output_price_per_million: float | None
    price_version: str
    config_version: str
    selected_from: str


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


class RoleModelResolver:
    """回退顺序：角色专用 → 推理模型 → 默认模型 → 确定性降级。"""

    def __init__(
        self, *, role_models: Mapping[str, Any] | None = None,
        reasoning_models: Iterable[Mapping[str, Any]] | None = None,
        default_models: Iterable[Mapping[str, Any]] | None = None,
        config_version: str = "default",
    ) -> None:
        self.role_models = dict(role_models or {})
        self.reasoning_models = [dict(item) for item in (reasoning_models or [])]
        self.default_models = [dict(item) for item in (default_models or [])]
        self.config_version = str(config_version or "default")

    def _candidate(self, value: dict[str, Any], selected_from: str) -> ResolvedModel | None:
        model = str(value.get("model") or "").strip()
        provider = str(value.get("provider") or "openai-compatible").strip()
        base_url = str(value.get("base_url") or "").strip()
        if not model or not base_url:
            return None
        classifications = tuple(dict.fromkeys(
            str(item) for item in value.get("data_classifications", ["public"]) if str(item)
        ))
        return ResolvedModel(
            provider=provider, base_url=base_url, model=model,
            key_ref=str(value.get("key_ref") or "backend_default"),
            supports_tools=bool(value.get("supports_tools", False)),
            context_tokens=max(0, int(value.get("context_tokens") or 0)),
            data_classifications=classifications,
            reasoning=bool(value.get("reasoning", False)),
            input_price_per_million=value.get("input_price_per_million"),
            output_price_per_million=value.get("output_price_per_million"),
            price_version=str(value.get("price_version") or "default"),
            config_version=self.config_version, selected_from=selected_from,
        )

    def resolve(
        self, role: str, requirements: ModelRequirements, budget: QaCostLedger,
    ) -> ResolvedModel:
        chain: list[tuple[str, dict[str, Any]]] = []
        chain.extend((f"role:{role}", item) for item in _items(self.role_models.get(role)))
        chain.extend(("reasoning", item) for item in self.reasoning_models)
        chain.extend(("default", item) for item in self.default_models)
        seen: set[tuple[str, str, str]] = set()
        for source, raw in chain:
            candidate = self._candidate(raw, source)
            if candidate is None:
                continue
            identity = (candidate.provider, candidate.base_url, candidate.model)
            if identity in seen:
                continue
            seen.add(identity)
            if requirements.supports_tools and not candidate.supports_tools:
                continue
            if candidate.context_tokens < requirements.min_context_tokens:
                continue
            if requirements.data_classification not in candidate.data_classifications:
                continue
            if requirements.reasoning_preferred and source == "reasoning" and not candidate.reasoning:
                continue
            budget.register_price(
                candidate.model, candidate.price_version,
                input_price_per_million=candidate.input_price_per_million,
                output_price_per_million=candidate.output_price_per_million,
            )
            if budget.max_cost is not None and not budget.can_price(candidate.model, candidate.price_version):
                continue
            return candidate
        raise LookupError("没有满足工具、上下文、数据权限和费用约束的可用模型")
