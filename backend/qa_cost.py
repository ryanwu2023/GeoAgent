"""本地问答的并发安全费用估算与预算预留。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4


class QaCostLimitExceeded(RuntimeError):
    """在发起模型调用前发现剩余预算不足。"""


@dataclass(frozen=True)
class CostReservation:
    reservation_id: str
    input_tokens: int
    output_tokens: int
    reserved_cny: float


class QaCostLedger:
    def __init__(
        self, *, input_price_per_million: float | None,
        output_price_per_million: float | None, max_cost_cny: float | None,
    ) -> None:
        self.input_price = input_price_per_million
        self.output_price = output_price_per_million
        self.max_cost = max_cost_cny
        self._estimated = 0.0
        self._reserved = 0.0
        self._input_tokens = 0
        self._output_tokens = 0
        self._reservations: dict[str, CostReservation] = {}
        self._lock = asyncio.Lock()

    @property
    def can_estimate(self) -> bool:
        return self.input_price is not None and self.output_price is not None

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        if not self.can_estimate:
            return 0.0
        return (
            max(0, input_tokens) * float(self.input_price)
            + max(0, output_tokens) * float(self.output_price)
        ) / 1_000_000

    async def reserve(self, input_tokens: int, output_tokens: int) -> CostReservation:
        reservation = CostReservation(
            uuid4().hex, max(0, int(input_tokens)), max(0, int(output_tokens)),
            self._cost(input_tokens, output_tokens),
        )
        async with self._lock:
            if self.max_cost is not None and self._estimated + self._reserved + reservation.reserved_cny > self.max_cost + 1e-12:
                raise QaCostLimitExceeded("单次问答估算费用将超过已配置上限")
            self._reserved += reservation.reserved_cny
            self._reservations[reservation.reservation_id] = reservation
        return reservation

    async def settle(self, reservation: CostReservation, *, output_tokens: int) -> None:
        async with self._lock:
            current = self._reservations.pop(reservation.reservation_id, None)
            if current is None:
                return
            self._reserved = max(0.0, self._reserved - current.reserved_cny)
            actual_output = max(0, int(output_tokens))
            self._input_tokens += current.input_tokens
            self._output_tokens += actual_output
            self._estimated += self._cost(current.input_tokens, actual_output)

    def snapshot(self) -> dict[str, object]:
        return {
            "estimate_available": self.can_estimate,
            "limit_enabled": self.max_cost is not None,
            "max_cost_cny": self.max_cost,
            "estimated_cost_cny": round(self._estimated, 6) if self.can_estimate else None,
            "reserved_cost_cny": round(self._reserved, 6) if self.can_estimate else None,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "currency": "CNY",
            "basis": "按配置单价与估算Token计算，不代表服务商账单",
        }
