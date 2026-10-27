import asyncio

import pytest

from backend.qa_cost import QaCostLedger, QaCostLimitExceeded


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
