"""Solvency monitor: reserves vs liabilities checks and alerts."""

import os
import pathlib
import subprocess
import sys
import time
from unittest.mock import AsyncMock

import pytest

from bot import solvency


@pytest.fixture(autouse=True)
def _reset_alert_state():
    """Both halves of the alert state, or the verdict leaks between tests.

    `_last_state` is what makes a persistent deficit one message instead of a
    standing emergency; left set by the previous test, a solvent check would answer
    «SOLVENCY RESTORED» to a test that asserts nothing was sent.
    """
    solvency._last_alert_ts = 0.0
    solvency._last_state = None
    yield
    solvency._last_alert_ts = 0.0
    solvency._last_state = None


@pytest.fixture(autouse=True)
def _mock_x402_reads(monkeypatch):
    """Keep the x402 reserve addends hermetic: no RPC, no DB in these tests."""
    monkeypatch.setattr(
        solvency.base, "receive_pool_balance", AsyncMock(return_value=0.0)
    )
    monkeypatch.setattr(
        solvency.ledger, "x402_unswept_credit_total", AsyncMock(return_value=0)
    )


@pytest.mark.asyncio
async def test_solvent_no_alert(monkeypatch):
    # reserves (100.0 USDC float, as base.hot_balance returns) > owed (60 USDC)
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=60_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=100.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_float_reserves_no_false_insolvency(monkeypatch):
    """Regression: base returns float dollars, ledger returns micro ints.
    Old code compared dollars to micro -> permanent false INSOLVENCY."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=60_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=100.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_insolvent_sends_alert(monkeypatch):
    # reserves (40.0) < owed (60), alert must fire
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=60_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=40.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_called_once()
    chat_id = bot.send_message.call_args[0][0]
    assert chat_id == "-100123"
    text = bot.send_message.call_args[0][1]
    assert "INSOLVENCY DETECTED" in text


@pytest.mark.asyncio
async def test_low_margin_alert(monkeypatch):
    # reserves (52.0) > owed (50), but buffer is only 4% (<5%), low margin alert
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=50_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=52.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_called_once()
    text = bot.send_message.call_args[0][1]
    assert "LOW MARGIN" in text


@pytest.mark.asyncio
async def test_cooldown_prevents_spam(monkeypatch):
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=60_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=40.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()

    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1

    # Second call within cooldown — must not send again
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_rpc_down_alerts_blind_canary(monkeypatch):
    """Regression: RPC failure must NOT silently skip — a canary that cannot
    see reserves is itself an emergency and must alert the operator."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=60_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(side_effect=ConnectionError("RPC down")))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_called_once()
    text = bot.send_message.call_args[0][1]
    assert "SOLVENCY CHECK BLIND" in text


@pytest.mark.asyncio
async def test_x402_pool_counts_toward_reserves(monkeypatch):
    """x402 receive pool backs booked liabilities but sits outside the hot
    wallet: it must count toward reserves or the canary false-alarms."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=60_000_000))
    # hot has 50 USDC, pool has 13 USDC -> 63 >= 60 (5% margin), solvent (no alert)
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=50.0))
    monkeypatch.setattr(
        solvency.base, "receive_pool_balance", AsyncMock(return_value=13.0)
    )
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_x402_unswept_derived_counts_toward_reserves(monkeypatch):
    """Credited-but-not-yet-swept x402 invoices are a liability AND the funds
    are still parked in the derived address: count them as reserve."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=60_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=50.0))
    monkeypatch.setattr(
        solvency.ledger, "x402_unswept_credit_total", AsyncMock(return_value=13_000_000)
    )
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_x402_pool_does_not_mask_real_insolvency(monkeypatch):
    """Pool addend helps, but genuine insolvency on the hot side still alerts."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=80_000_000))
    # hot 55 + pool 15 = 70 < 80 -> insolvent
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=55.0))
    monkeypatch.setattr(
        solvency.base, "receive_pool_balance", AsyncMock(return_value=15.0)
    )
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_called_once()
    text = bot.send_message.call_args[0][1]
    assert "INSOLVENCY DETECTED" in text


@pytest.mark.asyncio
async def test_no_alert_chat_id(monkeypatch):
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=60_000_000))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=40.0))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "")
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    bot.send_message.assert_not_called()



def _bad_ledger(monkeypatch, *, owed=60_000_000, hot=40.0) -> None:
    """Reserves and liabilities that answer the same verdict on every pass."""
    monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=owed))
    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=hot))
    monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", None)
    monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", "-100123")


@pytest.mark.asyncio
async def test_a_verdict_that_persists_speaks_once_then_on_the_slow_clock(monkeypatch):
    """The watcher wakes every 60 s and a deficit does not clear inside an hour, so a
    bare cooldown re-sent the identical INSOLVENCY text 288 times a day — which is
    what it started doing the wave the alert chain stopped ending at `0` and began
    resolving to `OWNER_ID`. A channel that cries every five minutes is a channel
    that gets muted, and this is the only warning a custodial ledger has."""
    _bad_ledger(monkeypatch)
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1

    # Past the five-minute floor, inside the repeat window: same verdict, nothing new.
    solvency._last_alert_ts = time.time() - solvency._ALERT_COOLDOWN - 1
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1

    # Past the repeat window the same verdict is said again, and says it is a repeat.
    solvency._last_alert_ts = time.time() - solvency._ALERT_REPEAT - 1
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 2
    text = bot.send_message.call_args[0][1]
    assert "INSOLVENCY DETECTED" in text and "still unresolved" in text


@pytest.mark.asyncio
async def test_a_recovered_ledger_says_so_once(monkeypatch):
    """An alert that never reports its own ending leaves the operator reading a
    resolved incident as a standing one. And a solvent ledger has nothing to remind."""
    _bad_ledger(monkeypatch)
    bot = AsyncMock()
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1

    monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=100.0))
    solvency._last_alert_ts = time.time() - solvency._ALERT_COOLDOWN - 1
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 2
    text = bot.send_message.call_args[0][1]
    assert "SOLVENCY RESTORED" in text and "insolvent" in text

    # Solvent is not a verdict to repeat every six hours.
    solvency._last_alert_ts = time.time() - solvency._ALERT_REPEAT - 1
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 2


@pytest.mark.asyncio
async def test_a_send_that_fails_does_not_consume_the_repeat_window(monkeypatch):
    """Telegram refusing the message must cost the next pass — a minute — not the
    whole `_ALERT_REPEAT`, and must not record a verdict nobody was told about."""
    _bad_ledger(monkeypatch)
    bot = AsyncMock()
    bot.send_message.side_effect = ConnectionError("telegram down")
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1
    assert solvency._last_state is None
    assert solvency._last_alert_ts == 0.0

    bot.send_message.side_effect = None
    await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 2
    assert solvency._last_state == "insolvent"


def test_the_repeat_window_is_longer_than_the_floor():
    """The two clocks only mean something in this order: `_ALERT_COOLDOWN` is the
    anti-spam floor every send obeys, `_ALERT_REPEAT` is how often an unchanged
    verdict may speak again. A repeat window at or under the floor is the old
    288-a-day behaviour wearing two constant names."""
    assert solvency._ALERT_REPEAT > solvency._ALERT_COOLDOWN


@pytest.mark.asyncio
async def test_a_blind_canary_logs_that_it_is_alive_and_blind(monkeypatch, caplog):
    """`SOLVENCY CHECK BLIND` reaches a chat; the log line is what tells a human
    reading `origin.out.log` that the watcher runs but cannot see reserves. Without
    it a blind canary and a dead one print the same thing: nothing."""
    _bad_ledger(monkeypatch)
    monkeypatch.setattr(
        solvency.base, "hot_balance", AsyncMock(side_effect=ConnectionError("RPC down"))
    )
    bot = AsyncMock()
    with caplog.at_level("INFO", logger="tipbot.solvency"):
        await solvency._check_solvency(bot)
    assert bot.send_message.call_count == 1
    assert solvency._last_state == "blind"
    lines = [rec.getMessage() for rec in caplog.records]
    assert any("blind" in line for line in lines), lines


def _chat_id_from_a_real_import(monkeypatch, *, alert: str, admin: str, owner: str) -> str:
    """Read `SOLVENCY_ALERT_CHAT_ID` out of a fresh interpreter with the three names
    set to the given (possibly empty) values. A subprocess rather than a reload: the
    chain is import-time arithmetic in `bot/config.py`, and reloading that module in
    this process would rewrite the knobs every other test in this session reads."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    env = {
        **os.environ,
        "PYTHONPATH": str(repo),
        "SOLVENCY_ALERT_CHAT_ID": alert,
        "ADMIN_TG_ID": admin,
        "OWNER_ID": owner,
    }
    out = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", "import bot.config as c; print(c.SOLVENCY_ALERT_CHAT_ID)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(repo), timeout=120,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout.strip()


def test_the_alert_chat_falls_back_to_the_owner_rather_than_dropping_the_alert(monkeypatch):
    """One knob decides where an insolvency alert, a low-margin alert and an AML flag
    go, and the chain used to end at `0` while `.env` on this host named only its
    owner — measured in `data/runtime/origin.err.log`, where a deficit of 363 USDC
    produced «no SOLVENCY_ALERT_CHAT_ID set, cannot send solvency alert» and nothing
    else. An empty value is passed rather than an absent one because that is the
    shape of a `.env` line written as `ADMIN_TG_ID=`, which is what the file carried.

    A blank in all three names still resolves to 0: the fallback widens who can be
    reached, it does not invent a recipient."""
    assert _chat_id_from_a_real_import(monkeypatch, alert="", admin="", owner="777001") == "777001"
    assert _chat_id_from_a_real_import(monkeypatch, alert="", admin="555", owner="777001") == "555"
    assert _chat_id_from_a_real_import(monkeypatch, alert="111", admin="555", owner="777001") == "111"
    assert _chat_id_from_a_real_import(monkeypatch, alert="", admin="", owner="") == "0"


@pytest.mark.asyncio
async def test_a_dropped_aml_flag_says_so_in_the_log(monkeypatch, caplog):
    """`_notify_aml` used to `return` on a missing chat with no trace at all, while
    the solvency watcher at least warned. A sanctioned counterparty is the one signal
    a wallet cannot re-derive later, so a refusal to deliver it is an event."""
    from bot.handlers import wallet

    monkeypatch.setattr(wallet.common.config, "SOLVENCY_ALERT_CHAT_ID", 0)
    bot = AsyncMock()
    with caplog.at_level("WARNING", logger="tipbot.wallet"):
        await wallet._notify_aml(bot, 4242, ["OFAC match"])
    bot.send_message.assert_not_called()
    lines = [rec.getMessage() for rec in caplog.records]
    assert any("AML flag" in line for line in lines), lines
