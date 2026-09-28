"""Paymaster usage persistence: increment_usage must persist to PostgreSQL."""

from bot.paymaster import FREE_TRANSACTIONS_COUNT, get_state, increment_usage


def test_paymaster_increment_persists(ledger):
    """increment_usage must write to DB, not just in-memory."""
    addr = "0xabcdef1234567890abcdef1234567890abcdef12"
    # Fresh start: zero usage
    state = get_state(addr)
    assert state.used_count == 0
    assert state.eligible is True

    # Increment once
    new_count = increment_usage(addr)
    assert new_count == 1

    # Re-read from DB: must persist
    state2 = get_state(addr)
    assert state2.used_count == 1
    assert state2.remaining == FREE_TRANSACTIONS_COUNT - 1


def test_paymaster_increment_multiple(ledger):
    """Multiple increments accumulate correctly."""
    addr = "0x9876543210fedcba9876543210fedcba98765432"
    for i in range(1, 4):
        count = increment_usage(addr)
        assert count == i

    state = get_state(addr)
    assert state.used_count == 3
    assert state.remaining == FREE_TRANSACTIONS_COUNT - 3


def test_paymaster_exhausts_allowance(ledger):
    """After FREE_TRANSACTIONS_COUNT increments, user is no longer eligible."""
    addr = "0x1111111111111111111111111111111111111111"
    for _ in range(FREE_TRANSACTIONS_COUNT):
        increment_usage(addr)

    state = get_state(addr)
    assert state.used_count == FREE_TRANSACTIONS_COUNT
    assert state.remaining == 0
    assert state.eligible is False
