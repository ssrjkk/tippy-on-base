"""Breakthrough Base features: gasless paymaster and recurring payments.

Made by @ssrjkk — github.com/ssrjkk.
"""

import time
from decimal import Decimal

import pytest

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


async def _insert_due_payment(async_ledger, payment_id: str, from_tg: int, to_tg: int,
                               amount_micro: int) -> None:
    """A schedule whose slot has already passed, written straight to the ledger."""
    past = int(time.time()) - 100
    await async_ledger.recurring_insert({
        "id": payment_id,
        "from_tg_id": from_tg,
        "to_tg_id": to_tg,
        "amount_micro": amount_micro,
        "interval": "daily",
        "memo": "Test subscription",
        "active": True,
        "created_at": int(time.time()),
        "next_execution": past,
        "last_execution": 0,
        "execution_count": 0,
        "max_executions": 0,
    })


@pytest.mark.asyncio
async def test_the_recurring_claim_answers_whether_it_took_the_slot(ledger):
    """mark_executed is the guard the executor debits on, so its verdict is the
    contract: the first claim of a slot is True, a second claim of the same slot
    and a claim of a payment that is not there are False."""
    from bot import ledger as ledger_mod

    store = RecurringPaymentStore(".")
    await _insert_due_payment(ledger_mod.async_ledger, "test_sub_claim_verdict", 511, 512, 5_000_000)

    assert await store.mark_executed("test_sub_claim_verdict") is True
    assert await store.mark_executed("test_sub_claim_verdict") is False
    assert await store.mark_executed("test_sub_no_such_row") is False


@pytest.mark.asyncio
async def test_a_recurring_pass_debits_only_for_a_slot_it_claimed(ledger, monkeypatch):
    """The pass used to send the transfer first and advance the schedule after,
    so a claim refused by another process still moved the money and a pass that
    crashed after the debit left the slot due. Claim first, obey the verdict."""
    import bot.ledger as ledger_mod
    import bot.main as bot_main
    from bot.main import recurring_pass

    store = RecurringPaymentStore(".")
    await _insert_due_payment(ledger_mod.async_ledger, "test_sub_pass_once", 521, 522, 5_000_000)
    ledger.credit(521, 40_000_000, "deposit")
    monkeypatch.setattr(bot_main, "ledger", ledger_mod.async_ledger)

    assert await recurring_pass(store) == 1
    assert ledger.balance(521) == Decimal("35.000000")
    assert ledger.balance(522) == Decimal("5.000000")

    # The same slot is not due again, so a second pass cannot pay it a second time.
    assert await recurring_pass(store) == 0
    assert ledger.balance(521) == Decimal("35.000000")
    assert ledger.balance(522) == Decimal("5.000000")

    # A due slot whose claim is lost reaches no transfer at all.
    async def claim_lost(payment_id):
        return False

    monkeypatch.setattr(store, "mark_executed", claim_lost)
    await _insert_due_payment(ledger_mod.async_ledger, "test_sub_pass_lost", 521, 522, 5_000_000)
    assert await recurring_pass(store) == 0
    assert ledger.balance(521) == Decimal("35.000000")
    assert ledger.balance(522) == Decimal("5.000000")
    row = await ledger_mod.async_ledger.recurring_get("test_sub_pass_lost")
    assert int(row["execution_count"]) == 0


@pytest.mark.asyncio
async def test_one_unclaimable_payment_does_not_stop_the_recurring_pass(ledger, monkeypatch):
    """A row the store cannot read (a schedule written by another version, a
    bookkeeping failure) used to be caught per payment inside the loop. Now the
    claim sits in the loop's own try, so the bad row costs its slot alone and the
    payments behind it are still made."""
    import bot.ledger as ledger_mod
    import bot.main as bot_main
    from bot.main import recurring_pass

    store = RecurringPaymentStore(".")
    await _insert_due_payment(ledger_mod.async_ledger, "test_sub_pass_poison", 541, 542, 5_000_000)
    await _insert_due_payment(ledger_mod.async_ledger, "test_sub_pass_after", 541, 543, 5_000_000)
    ledger.credit(541, 40_000_000, "deposit")
    monkeypatch.setattr(bot_main, "ledger", ledger_mod.async_ledger)

    real_claim = store.mark_executed

    async def claim_raises(payment_id):
        if payment_id == "test_sub_pass_poison":
            raise ValueError("unknown interval")
        return await real_claim(payment_id)

    monkeypatch.setattr(store, "mark_executed", claim_raises)

    assert await recurring_pass(store) == 1
    assert ledger.balance(542) == Decimal("0.000000")
    assert ledger.balance(543) == Decimal("5.000000")


@pytest.mark.asyncio
async def test_a_refused_recurring_transfer_is_not_counted_as_executed(ledger, monkeypatch):
    """transfer() answers whether money moved. When the payer cannot cover the
    period, nobody is credited, no tip row is written and the pass reports zero
    executed — while the claim still spends the slot, so the schedule moves on
    instead of retrying the same payment every hour."""
    import bot.ledger as ledger_mod
    import bot.main as bot_main
    from bot.main import recurring_pass

    store = RecurringPaymentStore(".")
    await _insert_due_payment(ledger_mod.async_ledger, "test_sub_pass_dry", 531, 532, 5_000_000)
    ledger.credit(531, 1_000_000, "deposit")
    monkeypatch.setattr(bot_main, "ledger", ledger_mod.async_ledger)

    assert await recurring_pass(store) == 0
    assert ledger.balance(532) == Decimal("0.000000")
    assert ledger.balance(531) == Decimal("1.000000")
    rows = ledger._conn.execute(
        "SELECT amount FROM tx_log WHERE kind = 'tip'").fetchall()
    assert rows == []
