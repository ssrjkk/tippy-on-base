"""Test that smart_buy balance check is atomic (no TOCTOU overspend race)."""

from unittest.mock import MagicMock

import pytest

from bot import smart_wallet as sw


def test_smart_buy_balance_check_under_lock(monkeypatch):
    """Balance check must happen under the user_op_lock to prevent race."""
    tg_id = 99999
    market_id = 1
    outcome = 0
    shares = 1000
    max_cost_micro = 5_000_000

    # Mock the dependencies
    mock_w3 = MagicMock()
    mock_acct = MagicMock()
    mock_w3.eth.account.from_key.return_value = mock_acct
    mock_w3.eth.chain_id = 8453
    mock_w3.eth.get_block.return_value = {"baseFeePerGas": 1000}
    mock_w3.to_wei = lambda amount, unit: int(amount * 1e9) if unit == "gwei" else int(amount)

    # Track lock acquisition
    lock_acquired = []
    original_lock = sw._user_op_lock

    def tracking_lock(tid):
        lock = original_lock(tid)
        class TrackedLock:
            def __enter__(self):
                lock_acquired.append(tid)
                return lock.__enter__()
            def __exit__(self, *args):
                return lock.__exit__(*args)
        return TrackedLock()

    monkeypatch.setattr(sw, "_user_op_lock", tracking_lock)
    monkeypatch.setattr(sw, "_get_w3", lambda: mock_w3)
    monkeypatch.setattr(sw, "predict_address", lambda tid: "0x" + "1" * 40)
    monkeypatch.setattr(sw, "is_deployed", lambda tid: True)
    monkeypatch.setattr(sw, "smart_nonce", lambda tid: 0)
    monkeypatch.setattr(sw, "_entrypoint", lambda: MagicMock())

    # Mock config
    from bot import config
    monkeypatch.setattr(config, "OUTCOME_MARKET_ADDRESS", "0x" + "2" * 40)
    monkeypatch.setattr(config, "USDC_ADDRESS", "0x" + "3" * 40)
    monkeypatch.setattr(config, "HOT_WALLET_KEY", "0x" + "4" * 64)
    monkeypatch.setattr(config, "SMART_WALLET_RELAYER_KEY", None)

    # Mock USDC contract to return insufficient balance
    mock_usdc = MagicMock()
    mock_usdc.functions.balanceOf.return_value.call.return_value = 1_000_000  # 1 USDC
    monkeypatch.setattr(sw, "_usdc", lambda: mock_usdc)

    # Mock other dependencies to avoid actual execution
    monkeypatch.setattr(sw, "_encode_execute_batch", lambda *args: b"")
    monkeypatch.setattr(sw, "_build_user_op", lambda **kwargs: {})
    monkeypatch.setattr(sw, "_sign_paymaster", lambda *args: b"")
    monkeypatch.setattr(sw, "_build_paymaster_data", lambda *args: b"")
    monkeypatch.setattr(sw, "_sign_user_op", lambda *args: b"")
    monkeypatch.setattr(sw, "_pack_user_op", lambda op: b"")

    # Mock market contract
    mock_market = MagicMock()
    mock_market.functions.buy.return_value.build_transaction.return_value = {"data": b""}
    monkeypatch.setattr(sw, "_market_contract", lambda w3, addr: mock_market)

    # The balance check should fail with RuntimeError
    with pytest.raises(RuntimeError, match="insufficient USDC"):
        sw.smart_buy_sync(tg_id, market_id, outcome, shares, max_cost_micro)

    # Verify the lock was acquired (balance check happened under lock)
    assert tg_id in lock_acquired, "Balance check must happen under user_op_lock"
