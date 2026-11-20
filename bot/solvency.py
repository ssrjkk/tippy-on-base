"""Real-time solvency monitor — checks reserves vs liabilities every cycle.

If liabilities > reserves at any point, sends an emergency Telegram alert.
This is the P0 monitoring: the canary in the coal mine for a custodial product.

Runs as an async task inside the bot process (no separate deployment).
"""
import asyncio
import logging
import time

from bot import base, config
from bot.ledger import async_ledger as ledger
from bot.pct import as_pct

log = logging.getLogger("tipbot.solvency")

_MICRO = 10 ** config.USDC_DECIMALS

# Alert state. `_last_state` is the verdict the operator was last told about, so a
# deficit that persists is one message and not a new emergency every minute.
_last_alert_ts = 0.0
_last_state: str | None = None
_ALERT_COOLDOWN = 300  # min 5 min between alerts to avoid spam
_ALERT_REPEAT = 6 * 3600  # while a verdict is unchanged, remind on this clock


async def solvency_watcher(bot, interval: int = 60) -> None:
    """Periodic solvency check. Alerts when the verdict changes, reminds while one stands."""
    while True:
        try:
            await _check_solvency(bot)
        except Exception as e:
            log.warning("solvency check failed: %s", e)
        await asyncio.sleep(interval)


async def _check_solvency(bot) -> None:
    try:
        owed = await ledger.total_liabilities()
    except Exception as e:
        log.warning("solvency: failed to read liabilities: %s", e)
        return

    # Get reserves
    vault_addr = config.VAULT_ADDRESS
    try:
        if vault_addr:
            reserves = await base.vault_balance()
        else:
            reserves = await base.hot_balance()
    except Exception:
        reserves = None

    if reserves is None:
        # RPC down: we literally cannot see the backing, so there is no
        # insolvency verdict. That blindness is itself an emergency — alert the
        # operator instead of quietly pretending it is fine. The warning is what
        # tells a human reading `origin.out.log` that the canary is alive and
        # blind; without it a blind watcher and a dead one print the same nothing.
        log.warning("solvency: blind, reserves unreadable, owed=%.2f", owed / _MICRO)
        await _report(
            bot,
            "blind",
            "🚨 <b>SOLVENCY CHECK BLIND</b>\n"
            "RPC unreachable — cannot verify reserves.\n"
            f"Liabilities as last read: ${owed / _MICRO:.2f}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        )
        return

    # x402 incoming: billed tips are a liability the moment they land in user
    # balances, but the USDC backing them sits in the per-payment derived
    # address (until the sweep consolidates it into the receive pool, and the
    # next sweep into the hot wallet). Neither address is the hot wallet/vault,
    # so without this addend the canary under-states reserves by the whole
    # in-flight x402 float whenever a sweep is delayed or the gas-drip daily
    # budget is exhausted. The two lines don't overlap on-chain: an invoice is
    # 'unswept' only while its funds are still in the derived address, and is
    # marked swept (so its parked amount drops out of the DB line) right after
    # the transfer has confirmed into the pool.
    try:
        reserves_micro = round(reserves * _MICRO) + await ledger.x402_unswept_credit_total()
    except Exception as e:
        log.warning("solvency: x402 unswept read failed: %s", e)
        reserves_micro = round(reserves * _MICRO)
    try:
        reserves_micro += round(await base.receive_pool_balance() * _MICRO)
    except Exception as e:
        log.warning("solvency: x402 pool read failed: %s", e)

    delta = reserves_micro - owed
    solvent = reserves_micro >= owed

    # Always log
    log.info(
        "solvency: reserves=%.2f owed=%.2f delta=%.2f solvent=%s",
        reserves_micro / _MICRO, owed / _MICRO, delta / _MICRO, solvent,
    )

    # Alert on insolvency or low margin (< 5% buffer)
    if not solvent:
        msg = (
            f"🚨 <b>INSOLVENCY DETECTED</b>\n"
            f"Reserves: ${reserves_micro / _MICRO:.2f}\n"
            f"Liabilities: ${owed / _MICRO:.2f}\n"
            f"Deficit: ${abs(delta) / _MICRO:.2f}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await _report(bot, "insolvent", msg)
    elif owed > 0 and delta / owed < 0.05:
        msg = (
            f"⚠️ <b>LOW MARGIN</b> ({as_pct(delta / owed)}%)\n"
            f"Reserves: ${reserves_micro / _MICRO:.2f}\n"
            f"Liabilities: ${owed / _MICRO:.2f}\n"
            f"Buffer: ${delta / _MICRO:.2f}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await _report(bot, "low_margin", msg)
    else:
        # Solvent with room: the verdict this function last announced, if any, is over.
        buffer_line = f" ({as_pct(delta / owed)}%)" if owed else ""
        await _report(
            bot,
            None,
            "✅ <b>SOLVENCY RESTORED</b>\n"
            f"Previous verdict: {_last_state}.\n"
            f"Reserves: ${reserves_micro / _MICRO:.2f}\n"
            f"Liabilities: ${owed / _MICRO:.2f}\n"
            f"Buffer: ${delta / _MICRO:.2f}{buffer_line}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        )


async def _report(bot, state: str | None, text: str) -> None:
    """Say each new verdict once, then repeat it only on the slow clock.

    The watcher wakes every 60 s and a deficit does not clear inside an hour, so a
    plain cooldown re-sent the identical INSOLVENCY text 288 times a day — which is
    what it began doing the wave the alert chain stopped ending at `0` and started
    resolving to `OWNER_ID`. A channel that cries every five minutes is a channel
    that gets muted, and this is the only warning a custodial ledger has. So a
    change of verdict, recovery included, is news and goes at once; an unchanged
    verdict is a reminder and goes at most every `_ALERT_REPEAT`. `_ALERT_COOLDOWN`
    still floors every send, so a verdict flapping between two bad states cannot
    spam either.

    A send that fails must not consume the repeat window: the clock and the
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
        if not await _send_alert(bot, text):
            return
        _last_state = None
        _last_alert_ts = now
        return
    if state == _last_state and now - _last_alert_ts < _ALERT_REPEAT:
        return
    if state == _last_state:
        text = f"{text}\n<i>Same verdict as the last alert — still unresolved after {(now - _last_alert_ts) / 3600:.1f} h.</i>"
    if not await _send_alert(bot, text):
        return
    _last_state = state
    _last_alert_ts = now


async def _send_alert(bot, text: str) -> bool:
    """Send alert to configured chat. Answers whether the operator actually got it."""
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
