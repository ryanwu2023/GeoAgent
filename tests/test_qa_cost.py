import asyncio

import pytest

from backend.qa_cost import QaCostLedger, QaCostLimitExceeded, QaPricingUnavailable


def test_unlimited_cost_ledger_allows_calls_without_prices():
    ledger = QaCostLedger(input_price_per_million=None, output_price_per_million=None, max_cost_cny=None)
    reservation = asyncio.run(ledger.reserve(1000, 2000))
    asyncio.run(ledger.settle(reservation, output_tokens=500))
    assert ledger.snapshot()["estimated_cost_cny"] is None
    assert ledger.snapshot()["limit_enabled"] is False


def test_concurrent_reservations_cannot_overbook_limit():
    async def scenario():
        ledger = QaCostLedger(input_price_per_million=100, output_price_per_million=100, max_cost_cny=0.15)
        first = await ledger.reserve(500, 500)
        with pytest.raises(QaCostLimitExceeded):
            await ledger.reserve(500, 500)
        await ledger.settle(first, output_tokens=200)
        second = await ledger.reserve(500, 200)
        await ledger.settle(second, output_tokens=200)
        return ledger.snapshot()

    result = asyncio.run(scenario())
    assert result["input_tokens"] == 1000
    assert result["output_tokens"] == 400
    assert result["estimated_cost_cny"] == 0.14


def test_specialist_budget_uses_saved_timeouts():
    from backend.qa_multiagent import SpecialistBudget

    budget = SpecialistBudget.from_settings({
        "max_parallel": 3,
        "per_agent_timeout_seconds": 123,
        "total_timeout_seconds": 456,
    })
    assert budget.max_parallel == 3
    assert budget.per_agent_seconds == 123
    assert budget.total_seconds == 456


def test_cost_ledger_restores_settled_usage_without_stale_reservations():
    ledger = QaCostLedger(input_price_per_million=10, output_price_per_million=20, max_cost_cny=1)
    ledger.restore({
        "estimated_cost_cny": 0.12, "reserved_cost_cny": 0.4,
        "input_tokens": 4000, "output_tokens": 4000,
    })
    snapshot = ledger.snapshot()
    assert snapshot["estimated_cost_cny"] == 0.12
    assert snapshot["reserved_cost_cny"] == 0.0
    assert snapshot["input_tokens"] == 4000
    assert snapshot["output_tokens"] == 4000


def test_model_specific_price_and_role_are_frozen_in_reservation():
    async def scenario():
        ledger = QaCostLedger(
            max_cost_cny=1,
            price_catalog={"reasoner@2026-10": {
                "input_price_per_million": 20, "output_price_per_million": 40,
            }},
        )
        reservation = await ledger.reserve(
            role="planner", model="reasoner", price_version="2026-10",
            input_tokens=1000, output_tokens=1000,
        )
        await ledger.settle(reservation, input_tokens=800, output_tokens=500)
        return reservation, ledger.snapshot()

    reservation, snapshot = asyncio.run(scenario())
    assert reservation.role == "planner"
    assert reservation.price_version == "2026-10"
    assert snapshot["estimated_cost_cny"] == 0.036
    assert snapshot["role_costs_cny"] == {"planner": 0.036}


def test_cost_limit_rejects_unpriced_model_but_unlimited_mode_allows_it():
    limited = QaCostLedger(max_cost_cny=1)
    with pytest.raises(QaPricingUnavailable):
        asyncio.run(limited.reserve(role="worker", model="unpriced", input_tokens=10, output_tokens=10))
    unlimited = QaCostLedger(max_cost_cny=None)
    reservation = asyncio.run(
        unlimited.reserve(role="worker", model="unpriced", input_tokens=10, output_tokens=10)
    )
    assert reservation.reserved_cny == 0


def test_unknown_settlement_keeps_hold_and_synthesis_reserve_has_priority():
    async def scenario():
        ledger = QaCostLedger(
            input_price_per_million=100, output_price_per_million=100,
            max_cost_cny=0.2, synthesis_reserve_cny=0.08,
        )
        with pytest.raises(QaCostLimitExceeded):
            await ledger.reserve(700, 700, role="worker", model="default")
        worker = await ledger.reserve(300, 300, role="worker", model="default")
        await ledger.settle(worker, output_tokens=None)
        synthesis = await ledger.reserve_synthesis(
            model="default", input_tokens=500, output_tokens=500,
        )
        return worker, synthesis, ledger.snapshot()

    worker, synthesis, snapshot = asyncio.run(scenario())
    assert worker.reservation_id in snapshot["unknown_reservation_ids"]
    assert snapshot["reserved_cost_cny"] == 0.16
    assert synthesis.priority is True
