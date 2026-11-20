"""Tests for the solvency verdict state machine, and the chaos law rewritten.

Every anchor is asserted before writing, so a moved line reports instead of
silently skipping the clause it exists to guard.
"""
import pathlib

T = pathlib.Path("tests/test_solvency.py")
C = pathlib.Path("tests/test_chaos.py")


def patch(path: pathlib.Path, pairs: list[tuple[str, str]]) -> None:
    s = path.read_text(encoding="utf-8-sig")
    for old, new in pairs:
        assert s.count(old) == 1, f"{path}: якорь не найден или не единственный ({s.count(old)}):\n{old[:160]}"
        s = s.replace(old, new, 1)
    path.write_bytes(s.encode("utf-8"))


FIXTURE_OLD = '''@pytest.fixture(autouse=True)
def _reset_alert_state():
    solvency._last_alert_ts = 0.0
    yield
    solvency._last_alert_ts = 0.0
'''
FIXTURE_NEW = '''@pytest.fixture(autouse=True)
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
'''

NEW_TESTS = '''
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


'''

patch(
    T,
    [
        ("import os\nimport pathlib\n", "import os\nimport pathlib\nimport time\n"),
        (FIXTURE_OLD, FIXTURE_NEW),
        ("def _chat_id_from_a_real_import(", NEW_TESTS + "def _chat_id_from_a_real_import("),
    ],
)

patch(
    C,
    [
        (
            "from unittest.mock import AsyncMock, MagicMock, patch",
            "import time\nfrom unittest.mock import AsyncMock, MagicMock, patch",
        ),
        (
            '''        """The canary must actually alert on a real deficit, and must not flood
        the operator: a second check inside the window stays silent."""
        from bot import solvency

        monkeypatch.setattr(solvency, "_last_alert_ts", 0.0)''',
            '''        """The canary must actually alert on a real deficit, and must not flood the
        operator. Two clocks hold the second half: the five-minute floor, and the
        fact that a verdict already reported is not news. Clearing the floor alone
        therefore must NOT re-send — that every-five-minutes repeat is exactly what
        silenced the channel once the alert chain resolved to a real owner."""
        from bot import solvency

        monkeypatch.setattr(solvency, "_last_alert_ts", 0.0)
        monkeypatch.setattr(solvency, "_last_state", None)''',
        ),
        (
            """        await solvency._check_solvency(bot)
        assert bot.send_message.await_count == 1  # cooldown holds

        monkeypatch.setattr(solvency, "_last_alert_ts", 0.0)
        await solvency._check_solvency(bot)
        assert bot.send_message.await_count == 2""",
            """        await solvency._check_solvency(bot)
        assert bot.send_message.await_count == 1  # the floor holds

        monkeypatch.setattr(solvency, "_last_alert_ts", time.time() - solvency._ALERT_COOLDOWN - 1)
        await solvency._check_solvency(bot)
        assert bot.send_message.await_count == 1  # same verdict, no repeat yet

        monkeypatch.setattr(solvency, "_last_alert_ts", time.time() - solvency._ALERT_REPEAT - 1)
        await solvency._check_solvency(bot)
        assert bot.send_message.await_count == 2  # the slow clock says it again""",
        ),
    ],
)

print("tests/test_solvency.py + tests/test_chaos.py: законы вердикта применены")
