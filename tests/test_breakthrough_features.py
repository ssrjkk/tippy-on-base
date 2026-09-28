"""Breakthrough Base features: gasless paymaster, recurring payments, credit
score, batch transactions.

Made by @ssrjkk — github.com/ssrjkk.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.credit import CreditScorer
from bot.paymaster import FREE_TRANSACTIONS_COUNT, check_eligibility, get_state
from bot.recurring import RecurrenceInterval, RecurringPaymentStore


@pytest.mark.asyncio
async def test_paymaster_gasless_eligibility():
    test_addr = "0x1234567890123456789012345678901234567890"
    info = await check_eligibility(test_addr)
    assert info["eligible"] is True
    assert info["remaining"] == FREE_TRANSACTIONS_COUNT
    assert info["total"] == FREE_TRANSACTIONS_COUNT

    state = get_state(test_addr)
    assert state.remaining == FREE_TRANSACTIONS_COUNT
    assert state.eligible is True


@pytest.mark.asyncio
async def test_recurring_payment_lifecycle(ledger):
    store = RecurringPaymentStore(".")
    payment = await store.create(
        payment_id="test_sub_1",
        from_tg_id=123456,
        to_tg_id=789012,
        amount_micro=10_000_000,  # $10
        interval=RecurrenceInterval.WEEKLY,
        memo="Test subscription",
    )
    assert payment.amount_micro == 10_000_000
    assert payment.interval == RecurrenceInterval.WEEKLY

    assert len(await store.list_for_user(123456)) == 1

    # Not due yet, due after a week, then cancels.
    assert len(await store.get_due(time.time())) == 0
    future = time.time() + 7 * 86400 + 100
    due = await store.get_due(future)
    assert len(due) == 1

    assert await store.cancel("test_sub_1", 123456) is True


@pytest.mark.asyncio
async def test_recurring_atomic_claim_prevents_double_execution(ledger):
    """The atomic claim (mark_executed) must be idempotent: calling it twice
    for the same payment must only advance the schedule once, so two concurrent
    executor loops cannot double-debit the same payment."""
    from bot import ledger as ledger_mod

    store = RecurringPaymentStore(".")
    # Insert directly with a past next_execution so the payment is due.
    past = int(time.time()) - 100
    await ledger_mod.async_ledger.recurring_insert({
        "id": "test_sub_atomic",
        "from_tg_id": 111,
        "to_tg_id": 222,
        "amount_micro": 5_000_000,
        "interval": "daily",
        "memo": "",
        "active": True,
        "created_at": int(time.time()),
        "next_execution": past,
        "last_execution": 0,
        "execution_count": 0,
        "max_executions": 0,
    })

    due = await store.get_due(time.time())
    assert len(due) == 1

    # First claim succeeds (advances schedule).
    await store.mark_executed("test_sub_atomic")
    row = await ledger_mod.async_ledger.recurring_get("test_sub_atomic")
    assert int(row["execution_count"]) == 1
    first_next = int(row["next_execution"])

    # Second claim for the same payment_id must NOT advance again (the
    # WHERE clause checks next_execution <= now, and we just pushed it
    # into the future).
    await store.mark_executed("test_sub_atomic")
    row2 = await ledger_mod.async_ledger.recurring_get("test_sub_atomic")
    assert int(row2["execution_count"]) == 1
    assert int(row2["next_execution"]) == first_next


@pytest.mark.asyncio
async def test_credit_score_bounds():
    mock_ledger = MagicMock()
    mock_ledger.user_stats = AsyncMock(return_value={
        "total_transactions": 50,
        "failed_transactions": 2,
        "total_volume_micro": 5000_000_000,  # $5000
    })
    mock_ledger.get_user_created = AsyncMock(return_value=time.time() - 180 * 86400)
    mock_ledger.count_markets_created = AsyncMock(return_value=5)
    mock_ledger.count_markets_participated = AsyncMock(return_value=15)
    mock_ledger.count_unique_peers = AsyncMock(return_value=12)

    scorer = CreditScorer(mock_ledger)
    credit = await scorer.calculate(123456)
    assert 300 <= credit.score <= 850
    assert credit.grade in ["A", "B", "C", "D", "F"]
    assert await scorer.get_max_loan(123456) > 0


@pytest.mark.asyncio
async def test_batch_executor_runs_actions():
    from bot.batch import ActionType, BatchAction, BatchExecutor

    mock_ledger = MagicMock()
    mock_ledger.tip = AsyncMock()
    mock_ledger.bet = AsyncMock()
    mock_ledger.create_market = AsyncMock(return_value=42)
    mock_ledger._lock = MagicMock()

    executor = BatchExecutor(mock_ledger)
    result = await executor.execute_batch(
        from_tg_id=123456,
        actions=[
            BatchAction(action_type=ActionType.TIP, params={
                "to_tg_id": 789012,
                "amount_micro": 5_000_000,
                "memo": "Test tip",
            }),
            BatchAction(action_type=ActionType.CREATE_MARKET, params={
                "question": "Test market?",
                "options": ["Yes", "No"],
            }),
        ],
    )
    assert result.success is True
    assert len(result.results) == 2
    assert mock_ledger.tip.called
    assert mock_ledger.create_market.called
