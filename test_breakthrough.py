#!/usr/bin/env python3
"""Test script for breakthrough features."""

import asyncio
import sys
import os

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.asyncio


async def test_paymaster():
    """Test gasless onboarding."""
    print("\n=== Testing Paymaster (Gasless Onboarding) ===")
    from bot.paymaster import check_eligibility, get_state, FREE_TRANSACTIONS_COUNT

    # Test with a dummy address
    test_addr = "0x1234567890123456789012345678901234567890"

    # Check initial state
    info = await check_eligibility(test_addr)
    print(f"[OK] Eligibility check: {info}")
    assert info["eligible"] is True
    assert info["remaining"] == FREE_TRANSACTIONS_COUNT
    assert info["total"] == FREE_TRANSACTIONS_COUNT

    # Get state
    state = get_state(test_addr)
    print(f"[OK] State created: remaining={state.remaining}, eligible={state.eligible}")

    print("[OK] Paymaster tests passed!")


async def test_recurring():
    """Test recurring payments."""
    print("\n=== Testing Recurring Payments ===")
    from bot.recurring import RecurringPaymentStore, RecurrenceInterval
    import tempfile
    import time

    # Create temp directory for test
    with tempfile.TemporaryDirectory() as tmpdir:
        store = RecurringPaymentStore(tmpdir)

        # Create a recurring payment
        payment = await store.create(
            payment_id="test_sub_1",
            from_tg_id=123456,
            to_tg_id=789012,
            amount_micro=10_000_000,  # $10
            interval=RecurrenceInterval.WEEKLY,
            memo="Test subscription",
        )
        print(f"[OK] Created payment: {payment.id}")
        assert payment.amount_micro == 10_000_000
        assert payment.interval == RecurrenceInterval.WEEKLY

        # List for user
        payments = await store.list_for_user(123456)
        print(f"[OK] Listed payments: {len(payments)} found")
        assert len(payments) == 1

        # Check due payments (should be none yet)
        due = await store.get_due(time.time())
        print(f"[OK] Due payments: {len(due)} (expected 0)")
        assert len(due) == 0

        # Check due payments in the future
        future_time = time.time() + 7 * 86400 + 100  # 1 week + 100 seconds
        due = await store.get_due(future_time)
        print(f"[OK] Due payments (future): {len(due)} (expected 1)")
        assert len(due) == 1

        # Cancel
        success = await store.cancel("test_sub_1", 123456)
        print(f"[OK] Cancelled: {success}")
        assert success is True

        print("[OK] Recurring payments tests passed!")


async def test_credit():
    """Test credit scoring."""
    print("\n=== Testing Credit Score ===")
    import time
    from bot.credit import CreditScorer
    from unittest.mock import AsyncMock, MagicMock

    # Mock ledger
    mock_ledger = MagicMock()
    mock_ledger.user_stats = AsyncMock(return_value={
        "total_transactions": 50,
        "failed_transactions": 2,
        "total_volume_micro": 5000_000_000,  # $5000
    })
    mock_ledger.get_user_created = AsyncMock(return_value=time.time() - 180 * 86400)  # 180 days ago
    mock_ledger.count_markets_created = AsyncMock(return_value=5)
    mock_ledger.count_markets_participated = AsyncMock(return_value=15)
    mock_ledger.count_unique_peers = AsyncMock(return_value=12)

    scorer = CreditScorer(mock_ledger)

    # Calculate credit score
    credit = await scorer.calculate(123456)
    print(f"[OK] Credit score: {credit.score} ({credit.grade})")
    print(f"  Confidence: {credit.confidence:.0%}")
    print(f"  Factors: {credit.factors}")

    assert 300 <= credit.score <= 850
    assert credit.grade in ["A", "B", "C", "D", "F"]

    # Get max loan
    max_loan = await scorer.get_max_loan(123456)
    print(f"[OK] Max loan: ${max_loan:.2f}")
    assert max_loan > 0

    print("[OK] Credit score tests passed!")


async def test_creator_tokens():
    """Test creator tokens."""
    print("\n=== Testing Creator Tokens ===")
    from bot.creator_tokens import CreatorTokenRegistry
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        registry = CreatorTokenRegistry(tmpdir)

        # Create a token
        token = await registry.create_token(
            creator_tg_id=123456,
            name="Test Token",
            symbol="TST",
            total_supply=1_000_000,
            initial_price_micro=100_000,  # $0.10
        )
        print(f"[OK] Created token: {token.name} (${token.symbol})")
        print(f"  Token ID: {token.token_id}")

        # Buy tokens
        success, cost = await registry.buy_tokens(
            token_id=token.token_id,
            buyer_tg_id=789012,
            amount=1000,
        )
        print(f"[OK] Bought 1000 tokens, cost: ${cost / 1e6:.2f}")
        assert success is True

        # Get holder info
        info = await registry.get_holder_info(token.token_id, 789012)
        print(f"[OK] Holder balance: {info['balance']} tokens")
        assert info["balance"] == 1000

        # Distribute dividend
        dividend = await registry.distribute_dividend(
            token_id=token.token_id,
            amount_micro=5_000_000,  # $5
        )
        print(f"[OK] Distributed dividend: ${dividend.amount_micro / 1e6:.2f}")
        assert dividend is not None

        # Check pending dividends
        info = await registry.get_holder_info(token.token_id, 789012)
        print(f"[OK] Pending dividends: ${info['pending_dividends_micro'] / 1e6:.2f}")
        assert info["pending_dividends_micro"] > 0

        # Claim dividends
        claimed = await registry.claim_dividends(token.token_id, 789012)
        print(f"[OK] Claimed: ${claimed / 1e6:.2f}")
        assert claimed > 0

        # Verify claimed
        info = await registry.get_holder_info(token.token_id, 789012)
        print(f"[OK] Pending after claim: ${info['pending_dividends_micro'] / 1e6:.2f}")
        assert info["pending_dividends_micro"] == 0

        print("[OK] Creator tokens tests passed!")


async def test_batch():
    """Test batch transactions."""
    print("\n=== Testing Batch Transactions ===")
    from bot.batch import BatchExecutor, BatchAction, ActionType
    from unittest.mock import AsyncMock, MagicMock
    import asyncio

    # Mock ledger
    mock_ledger = MagicMock()
    mock_ledger.tip = AsyncMock()
    mock_ledger.bet = AsyncMock()
    mock_ledger.create_market = AsyncMock(return_value=42)
    mock_ledger._lock = asyncio.Lock()

    executor = BatchExecutor(mock_ledger)

    # Create batch actions
    actions = [
        BatchAction(action_type=ActionType.TIP, params={
            "to_tg_id": 789012,
            "amount_micro": 5_000_000,
            "memo": "Test tip",
        }),
        BatchAction(action_type=ActionType.CREATE_MARKET, params={
            "question": "Test market?",
            "options": ["Yes", "No"],
        }),
    ]

    # Execute batch
    result = await executor.execute_batch(from_tg_id=123456, actions=actions)
    print(f"[OK] Batch executed: success={result.success}")
    print(f"  Results: {len(result.results)} actions")
    print(f"  Gas saved: {result.gas_saved_percent:.1f}%")

    assert result.success is True
    assert len(result.results) == 2
    assert result.gas_saved_percent > 0

    # Verify calls
    assert mock_ledger.tip.called
    assert mock_ledger.create_market.called

    print("[OK] Batch transactions tests passed!")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("TESTING BREAKTHROUGH BASE FEATURES")
    print("=" * 60)

    try:
        await test_paymaster()
        await test_recurring()
        await test_credit()
        await test_creator_tokens()
        await test_batch()

        print("\n" + "=" * 60)
        print("[OK] ALL TESTS PASSED!")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n[FAIL] TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
