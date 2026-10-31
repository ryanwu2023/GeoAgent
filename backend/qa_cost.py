"""本地问答跨角色共享的并发安全费用账本。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4


class QaCostLimitExceeded(RuntimeError):
    """在发起模型调用前发现剩余预算不足。"""


class QaPricingUnavailable(RuntimeError):
    """启用费用上限后，候选模型没有完整价格。"""


@dataclass(frozen=True)
class CostReservation:
    reservation_id: str
    input_tokens: int
    output_tokens: int
    reserved_cny: float
    role: str = "default"
    model: str = "default"
    price_version: str = "default"
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    priority: bool = False


class QaCostLedger:
    def __init__(
        self, *, input_price_per_million: float | None = None,
        output_price_per_million: float | None = None, max_cost_cny: float | None = None,
        price_catalog: Mapping[str, Mapping[str, Any]] | None = None,
        synthesis_reserve_cny: float = 0.0,
    ) -> None:
        self.input_price = input_price_per_million
        self.output_price = output_price_per_million
        self.max_cost = max_cost_cny
        self.price_catalog = {str(key): dict(value) for key, value in (price_catalog or {}).items()}
        self.synthesis_reserve_cny = max(0.0, float(synthesis_reserve_cny or 0.0))
        self._estimated = 0.0
        self._reserved = 0.0
        self._input_tokens = 0
        self._output_tokens = 0
        self._reservations: dict[str, CostReservation] = {}
        self._unknown_reservations: set[str] = set()
        self._role_costs: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def restore(self, snapshot: dict[str, object] | None) -> "QaCostLedger":
        """恢复已结算用量；未知结算继续占用保存下来的预留费用。"""
        value = snapshot if isinstance(snapshot, dict) else {}
        estimated = value.get("estimated_cost_cny")
        available = bool(value.get("estimate_available", self.can_estimate))
        self._estimated = max(0.0, float(estimated or 0.0)) if available else 0.0
        self._input_tokens = max(0, int(value.get("input_tokens") or 0))
        self._output_tokens = max(0, int(value.get("output_tokens") or 0))
        self._reservations.clear()
        self._unknown_reservations = {
            str(item) for item in (value.get("unknown_reservation_ids") or []) if str(item)
        }
        self._reserved = (
            max(0.0, float(value.get("reserved_cost_cny") or 0.0))
            if self._unknown_reservations else 0.0
        )
        role_costs = value.get("role_costs_cny")
        self._role_costs = {
            str(key): max(0.0, float(amount))
            for key, amount in (role_costs.items() if isinstance(role_costs, dict) else [])
        }
        return self

    @property
    def can_estimate(self) -> bool:
        default_prices = self.input_price is not None and self.output_price is not None
        return default_prices or bool(self.price_catalog)

    def _prices(self, model: str, price_version: str) -> tuple[float | None, float | None]:
        for key in (f"{model}@{price_version}", model, "default"):
            value = self.price_catalog.get(key)
            if value is not None:
                return value.get("input_price_per_million"), value.get("output_price_per_million")
        return self.input_price, self.output_price

    def can_price(self, model: str, price_version: str = "default") -> bool:
        input_price, output_price = self._prices(model, price_version)
        return input_price is not None and output_price is not None

    def register_price(
        self, model: str, price_version: str, *,
        input_price_per_million: float | None, output_price_per_million: float | None,
    ) -> None:
        if input_price_per_million is None or output_price_per_million is None:
            return
        self.price_catalog[f"{model}@{price_version}"] = {
            "input_price_per_million": float(input_price_per_million),
            "output_price_per_million": float(output_price_per_million),
        }

    @staticmethod
    def _cost(
        input_tokens: int, output_tokens: int,
        input_price: float | None, output_price: float | None,
    ) -> float:
        if input_price is None or output_price is None:
            return 0.0
        return (
            max(0, input_tokens) * float(input_price)
            + max(0, output_tokens) * float(output_price)
        ) / 1_000_000

    async def reserve(
        self, input_tokens: int | None = None, output_tokens: int | None = None, *,
        role: str = "default", model: str = "default", price_version: str = "default",
        priority: bool = False,
    ) -> CostReservation:
        input_count = max(0, int(input_tokens or 0))
        output_count = max(0, int(output_tokens or 0))
        input_price, output_price = self._prices(model, price_version)
        if self.max_cost is not None and (input_price is None or output_price is None):
            raise QaPricingUnavailable("已设置费用上限，但候选模型缺少输入或输出价格")
        reservation = CostReservation(
            uuid4().hex, input_count, output_count,
            self._cost(input_count, output_count, input_price, output_price),
            role=str(role or "default"), model=str(model or "default"),
            price_version=str(price_version or "default"),
            input_price_per_million=input_price, output_price_per_million=output_price,
            priority=bool(priority),
        )
        async with self._lock:
            hold = 0.0 if priority else self.synthesis_reserve_cny
            projected = self._estimated + self._reserved + reservation.reserved_cny + hold
            if self.max_cost is not None and projected > self.max_cost + 1e-12:
                raise QaCostLimitExceeded("单次问答估算费用将超过已配置上限")
            self._reserved += reservation.reserved_cny
            self._reservations[reservation.reservation_id] = reservation
        return reservation

    async def reserve_synthesis(
        self, *, model: str, price_version: str = "default",
        input_tokens: int, output_tokens: int,
    ) -> CostReservation:
        return await self.reserve(
            input_tokens, output_tokens, role="synthesizer", model=model,
            price_version=price_version, priority=True,
        )

    async def settle(
        self, reservation: CostReservation, *, output_tokens: int | None,
        input_tokens: int | None = None,
    ) -> None:
        async with self._lock:
            current = self._reservations.get(reservation.reservation_id)
            if current is None:
                return
            if output_tokens is None:
                self._unknown_reservations.add(current.reservation_id)
                return
            self._reservations.pop(current.reservation_id, None)
            self._unknown_reservations.discard(current.reservation_id)
            self._reserved = max(0.0, self._reserved - current.reserved_cny)
            actual_input = current.input_tokens if input_tokens is None else max(0, int(input_tokens))
            actual_output = max(0, int(output_tokens))
            self._input_tokens += actual_input
            self._output_tokens += actual_output
            actual_cost = self._cost(
                actual_input, actual_output,
                current.input_price_per_million, current.output_price_per_million,
            )
            self._estimated += actual_cost
            self._role_costs[current.role] = self._role_costs.get(current.role, 0.0) + actual_cost

    async def release(self, reservation: CostReservation) -> None:
        """仅供明确未发送或明确未计费的调用释放预留。"""
        async with self._lock:
            current = self._reservations.pop(reservation.reservation_id, None)
            if current is None:
                return
            self._unknown_reservations.discard(current.reservation_id)
            self._reserved = max(0.0, self._reserved - current.reserved_cny)

    def snapshot(self) -> dict[str, object]:
        estimate_available = self.can_estimate
        return {
            "estimate_available": estimate_available,
            "limit_enabled": self.max_cost is not None,
            "max_cost_cny": self.max_cost,
            "estimated_cost_cny": round(self._estimated, 6) if estimate_available else None,
            "reserved_cost_cny": round(self._reserved, 6) if estimate_available else None,
            "synthesis_reserve_cny": self.synthesis_reserve_cny,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "unknown_reservation_ids": sorted(self._unknown_reservations),
            "role_costs_cny": {key: round(value, 6) for key, value in self._role_costs.items()},
            "currency": "CNY",
            "basis": "按冻结的模型价格版本与实际或估算Token计算，不代表服务商账单",
        }
