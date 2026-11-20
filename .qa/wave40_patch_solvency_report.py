"""Make the solvency canary report a verdict change, not a heartbeat.

Applied as a script so every anchor is asserted before anything is written: a
silent no-op patch would leave the 288-messages-a-day behaviour in place and look
like a fix.
"""
import pathlib
import sys

P = pathlib.Path("bot/solvency.py")
s = P.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global s
    assert s.count(old) == 1, f"якорь не найден или не единственный ({s.count(old)}):\n{old[:120]}"
    s = s.replace(old, new, 1)


# 1. state: the verdict itself, plus the two clocks.
sub(
    """# Alert state
_last_alert_ts = 0.0
_ALERT_COOLDOWN = 300  # min 5 min between alerts to avoid spam
""",
    """# Alert state. `_last_state` is the verdict the operator was last told about, so a
# deficit that persists is one message and not a new emergency every minute.
_last_alert_ts = 0.0
_last_state: str | None = None
_ALERT_COOLDOWN = 300  # min 5 min between alerts to avoid spam
_ALERT_REPEAT = 6 * 3600  # while a verdict is unchanged, remind on this clock
""",
)

# 2. the watcher's globals.
sub(
    """async def solvency_watcher(bot, interval: int = 60) -> None:
    \"\"\"Periodic solvency check. Sends Telegram alert if insolvent.\"\"\"
    global _last_alert_ts
""",
    """async def solvency_watcher(bot, interval: int = 60) -> None:
    \"\"\"Periodic solvency check. Alerts on a verdict change, reminds while one stands.\"\"\"
""",
)

# 3. the blind branch: log the blindness, hand the verdict to the reporter.
sub(
    """    if reserves is None:
        # RPC down: we literally cannot see the backing, so there is no
        # insolvency verdict. That blindness is itself an emergency — alert the
        # operator (rate-limited) instead of quietly pretending it is fine.
        now = time.time()
        if now - _last_alert_ts >= _ALERT_COOLDOWN:
            _last_alert_ts = now
            await _send_alert(
                bot,
                "🚨 <b>SOLVENCY CHECK BLIND</b>\\n"
                "RPC unreachable — cannot verify reserves.\\n"
                f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            )
        return
""",
    """    if reserves is None:
        # RPC down: we literally cannot see the backing, so there is no
        # insolvency verdict. That blindness is itself an emergency — alert the
        # operator instead of quietly pretending it is fine. The log line is what
        # tells a human reading `origin.out.log` that the canary is alive and
        # blind, rather than dead: without it the watcher's silence was
        # indistinguishable from a watcher that never started.
        log.warning("solvency: blind, reserves unreadable (RPC/vault down), owed=%.2f", owed / _MICRO)
        await _report(
            bot,
            "blind",
            "🚨 <b>SOLVENCY CHECK BLIND</b>\\n"
            "RPC unreachable — cannot verify reserves.\\n"
            f"Liabilities as last read: ${owed / _MICRO:.2f}\\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        )
        return
""",
)

# 4. the two verdict branches: same facts, but reported through the state machine.
sub(
    """    # Alert on insolvency or low margin (< 5% buffer)
    now = time.time()
    if now - _last_alert_ts < _ALERT_COOLDOWN:
        return

    if not solvent:
        _last_alert_ts = now
        msg = (
""",
    """    # Alert on insolvency or low margin (< 5% buffer)
    if not solvent:
        msg = (
""",
)
sub(
    """            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await _send_alert(bot, msg)
    elif owed > 0 and delta / owed < 0.05:
        _last_alert_ts = now
        msg = (
""",
    """            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await _report(bot, "insolvent", msg)
    elif owed > 0 and delta / owed < 0.05:
        msg = (
""",
)
sub(
    """            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await _send_alert(bot, msg)


async def _send_alert(bot, text: str) -> None:
    \"\"\"Send alert to configured chat.\"\"\"
    chat_id = config.SOLVENCY_ALERT_CHAT_ID
    if not chat_id:
        log.warning("no SOLVENCY_ALERT_CHAT_ID set, cannot send solvency alert")
        return
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        log.warning("failed to send solvency alert: %s", e)
""",
    """            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await _report(bot, "low_margin", msg)
    else:
        await _report(bot, None, "")


async def _report(bot, state: str | None, text: str) -> None:
    """Say each new verdict once, then repeat it only on the slow clock.

    The watcher wakes every 60 s and a deficit does not clear inside an hour, so a
    plain cooldown re-sent the identical INSOLVENCY text 288 times a day — which is
    what it does now that the alert chain resolves to `OWNER_ID` instead of ending
    at `0`. A channel that cries every five minutes is a channel that gets muted,
    and the muted one is the only warning this product has. So: a change of verdict
    (including recovery) is news and goes immediately; an unchanged verdict is a
    reminder and goes at most every `_ALERT_REPEAT`. `_ALERT_COOLDOWN` still floors
    every send, so a verdict flapping between two bad states cannot spam.

    A send that fails must not consume the repeat window: the timestamp and the
    remembered verdict advance only once the message is out, so the next pass — a
    minute later — retries instead of waiting six hours to say the same thing.
    """
    global _last_alert_ts, _last_state
    now = time.time()
    if now - _last_alert_ts < _ALERT_COOLDOWN:
        return
    if state is None:
        if _last_state is None:
            return
        if not await _send_alert(
            bot,
            "✅ <b>SOLVENCY RESTORED</b>\\n"
            f"Previous verdict: {_last_state}. Reserves cover liabilities again.\\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        ):
            return
        _last_state = None
        _last_alert_ts = now
        return
    if state == _last_state and now - _last_alert_ts < _ALERT_REPEAT:
        return
    if state == _last_state:
        text = f"{text}\\n<i>Same verdict as before — still unresolved after {(now - _last_alert_ts) / 3600:.1f} h.</i>"
    if not await _send_alert(bot, text):
        return
    _last_state = state
    _last_alert_ts = now


async def _send_alert(bot, text: str) -> bool:
    \"\"\"Send alert to configured chat. Answers whether the operator actually got it.\"\"\"
    chat_id = config.SOLVENCY_ALERT_CHAT_ID
    if not chat_id:
        log.warning("no SOLVENCY_ALERT_CHAT_ID set, cannot send solvency alert")
        return False
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        log.warning("failed to send solvency alert: %s", e)
        return False
    return True
""",
)

P.write_bytes(s.encode("utf-8"))
print("bot/solvency.py: состояние вердикта, часы повтора, лог слепоты, честная отправка — применено")
sys.exit(0)
