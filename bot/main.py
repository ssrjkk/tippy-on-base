"""Tippy entrypoint. Run: python -m bot.main. Made by @ssrjkk — github.com/ssrjkk"""
import asyncio
import logging
import os
import signal
import time
from collections.abc import Callable, Coroutine, Iterable
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand

from . import base, config, i18n
from .commands_catalog import BOT_COMMAND_SPECS
from .handlers import router
from .handlers.onchain import onchain_watcher
from .ledger import async_ledger as ledger
from .solvency import solvency_watcher
from .telegram_transport import make_session

# Structured JSON logs for production (LOG_FORMAT=json), human-readable for dev.
if os.environ.get("LOG_FORMAT") == "json":
    from pythonjsonlogger.json import JsonFormatter
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

log = logging.getLogger("tipbot")
_session = make_session(config.TELEGRAM_API_PROXY, config.TELEGRAM_API_IP)
_bot_kwargs = {"session": _session} if _session else {}
bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML), **_bot_kwargs)

async def deposit_watcher() -> None:
    backoff = config.POLL_SECONDS
    while True:
        try:
            credited = await base.poll_deposits()
            backoff = config.POLL_SECONDS  # reset on success
        except Exception as e:
            log.warning('deposit poll failed (backoff %ds): %s', backoff, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)
            continue
        for d in credited:
            try:
                settings = await ledger.get_settings(int(d['tg_id']))
                if not settings['notify_deposits']:
                    continue
                text = i18n.t(i18n.norm(settings.get('lang')), 'deposit_notified', amount=f"{d['amount_micro'] / 10 ** config.USDC_DECIMALS:g}", tx_url=f"{config.BASESCAN_URL}/tx/{d['tx_hash']}", tx=d['tx_hash'][:18])
                await ledger.enqueue_notification(int(d['tg_id']), text)
            except Exception as e:
                log.warning('deposit notify enqueue failed for %s: %s', d['tg_id'], e)
        await asyncio.sleep(config.POLL_SECONDS)

async def withdraw_watcher() -> None:
    while True:
        try:
            await base.check_pending_withdraws()
        except Exception as e:
            log.warning('withdraw check failed: %s', e)
        await asyncio.sleep(config.POLL_SECONDS)

async def batch_withdraw_watcher() -> None:
    """Flush queued withdrawals through TipBotVault.batchDistribute.

    /withdraw enqueues; this watcher flushes the queue (gaas-saving batched
    payout) whenever a flush condition is met. Runs on a shorter cadence than
    the slow refund sweep so isolated withdrawals still land promptly.
    """
    while True:
        try:
            await base.flush_withdraw_batch()
        except Exception as e:
            log.warning('batch withdraw flush failed: %s', e)
        await asyncio.sleep(config.WITHDRAW_BATCH_FLUSH_SECONDS)

async def market_watcher() -> None:
    """Once per cycle: remind market creators to resolve markets whose deadline
    passed (both parimutuel bets and LMSR AMM markets). Without resolution the
    traders' money sits locked until the grace refund, so one nudge prevents
    'forgotten markets'. A second, final nudge goes out shortly before the
    grace period ends (after that anyone can refund)."""
    while True:
        try:
            for bet in await ledger.open_bets_past_deadline():
                await ledger.mark_deadline_notified(int(bet['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(bet['creator'])).get('lang'))
                    text = i18n.t(creator_lang, 'deadline_notify', id=bet['id'], question=bet['question'])
                    await ledger.enqueue_notification(bet['creator'], text)
                except Exception as e:
                    log.warning('deadline notify enqueue failed for #%s: %s', bet['id'], e)
            for bet in await ledger.bets_need_grace_warning(config.GRACE_WARN_BEFORE_HOURS * 3600):
                hours_left = max(1, round((bet['close_at'] + config.MARKET_GRACE_HOURS * 3600 - time.time()) / 3600))
                await ledger.mark_grace_warned(int(bet['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(bet['creator'])).get('lang'))
                    text = i18n.t(creator_lang, 'grace_warn', id=bet['id'], question=bet['question'], hours=hours_left)
                    await ledger.enqueue_notification(bet['creator'], text)
                except Exception as e:
                    log.warning('grace warn enqueue failed for #%s: %s', bet['id'], e)
            for m in await ledger.open_markets_past_deadline():
                await ledger.mark_market_deadline_notified(int(m['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(m['creator'])).get('lang'))
                    text = i18n.t(creator_lang, 'deadline_notify', id=m['id'], question=m['question'])
                    await ledger.enqueue_notification(m['creator'], text)
                except Exception as e:
                    log.warning('market deadline notify enqueue failed for #%s: %s', m['id'], e)
            for m in await ledger.markets_need_grace_warning(config.GRACE_WARN_BEFORE_HOURS * 3600):
                hours_left = max(1, round((m['close_at'] + config.MARKET_GRACE_HOURS * 3600 - time.time()) / 3600))
                await ledger.mark_market_grace_warned(int(m['id']))
                try:
                    creator_lang = i18n.norm((await ledger.get_settings(m['creator'])).get('lang'))
                    text = i18n.t(creator_lang, 'grace_warn', id=m['id'], question=m['question'], hours=hours_left)
                    await ledger.enqueue_notification(m['creator'], text)
                except Exception as e:
                    log.warning('market grace warn enqueue failed for #%s: %s', m['id'], e)
        except Exception as e:
            log.warning('market deadline check failed: %s', e)
        await asyncio.sleep(config.POLL_SECONDS * 4)

async def channel_watcher() -> None:
    """Kick users whose paid channel access expired (once per few cycles)."""
    while True:
        try:
            await base.kick_expired_channel_subscriptions(bot)
        except Exception as e:
            log.warning('channel kick check failed: %s', e)
        await asyncio.sleep(config.POLL_SECONDS * 4)

async def create2_sweep_watcher() -> None:
    """Move USDC from per-user CREATE2 proxies to the hot wallet.

    The proxy itself only holds funds; until ``forward()`` runs the deposit
    scanner (which watches the hot wallet) never sees them. Runs alongside the
    deposit poller; idle when CREATE2 is disabled or no proxy holds USDC.
    """
    from bot.create2 import is_create2_enabled, sweep_all_proxies
    while True:
        try:
            if is_create2_enabled():
                swept = await sweep_all_proxies()
                if swept:
                    log.info('create2 sweep: forwarded for %s', swept)
        except Exception as e:
            log.warning('create2 sweep failed: %s', e)
        await asyncio.sleep(config.POLL_SECONDS)

async def x402_sweep_watcher() -> None:
    """Consolidate USDC from per-invoice pay addresses to the x402 receive pool.

    Each x402 invoice derives a unique EOA that holds USDC after payment. This
    watcher moves those funds to the shared X402_RECEIVE_ADDRESS so they are
    visible to the same reconciliation logic the existing x402 flow uses.
    Idle when x402 is disabled or no invoice address holds USDC.
    """
    from bot.x402_sweep import sweep_all_invoices
    while True:
        try:
            if config.X402_ENABLED and config.X402_RECEIVE_ADDRESS:
                swept = await sweep_all_invoices()
                if swept:
                    log.info('x402 sweep: consolidated %d invoice(s)', swept)
        except Exception as e:
            log.warning('x402 sweep failed: %s', e)
        await asyncio.sleep(config.POLL_SECONDS)

async def housekeeping_watcher() -> None:
    """Daily DB housekeeping: prune the reaction-tip message index and the
    x402/deposit side-tables so the DB stays bounded in active groups
    (balances live in `users`, so no money-critical data is touched — paid
    x402 txs are kept for a full year as the anti-replay guard)."""
    while True:
        try:
            removed = await ledger.prune_message_index(config.MESSAGE_INDEX_RETENTION_SECONDS)
            if removed:
                log.info('pruned %s stale message-index rows', removed)
            counts = await ledger.prune_housekeeping(
                getattr(config, "X402_RETENTION_SECONDS", 90 * 86400),
                getattr(config, "X402_PAYMENT_RETENTION_SECONDS", 365 * 86400),
            )
            if any(counts.values()):
                log.info('pruned x402/pending rows: %s', counts)
        except Exception as e:
            log.warning('housekeeping failed: %s', e)
        await asyncio.sleep(getattr(config, "HOUSEKEEPING_INTERVAL_SECONDS", 86400))


async def recurring_payment_executor() -> None:
    """Execute due recurring payments (subscriptions)."""
    from .recurring import store

    while True:
        try:
            now = time.time()
            due = await store.get_due(now)
            for payment in due:
                try:
                    await ledger.transfer(payment.from_tg_id, payment.to_tg_id, payment.amount_micro, payment.memo or "Recurring payment")
                    await store.mark_executed(payment.id)
                    log.info('executed recurring payment %s: %s → %s $%s',
                             payment.id, payment.from_tg_id, payment.to_tg_id,
                             payment.amount_micro / 1e6)
                except Exception as e:
                    log.warning('recurring payment %s failed: %s', payment.id, e)
        except Exception as e:
            log.warning('recurring executor failed: %s', e)
        await asyncio.sleep(3600)  # Check every hour


async def x402_reconcile_watcher() -> None:
    # Imported here, not at module scope: bot.main must not pull the web layer
    # (and its ledger-bound modules) in just to start the bot.
    from web.x402 import reconcile_stale_x402

    while True:
        try:
            n = await reconcile_stale_x402()
            if n:
                log.warning('x402 reconcile finalized %d stale payment(s)', n)
        except Exception as e:
            log.warning('x402 reconcile failed: %s', e)
        await asyncio.sleep(config.POLL_SECONDS * 8)


async def notification_outbox_worker() -> None:
    """Drain queued Telegram notifications with retry logic."""
    while True:
        try:
            items = await ledger.dequeue_notifications()
            for n in items:
                try:
                    await bot.send_message(n['chat_id'], n['text'])
                    await ledger.ack_notification(n['id'])
                except Exception:
                    await ledger.retry_notification(n['id'], 30)
        except Exception as e:
            log.warning('notification outbox worker failed: %s', e)
        await asyncio.sleep(5)


# The watcher set this process runs, as (name, zero-arg coroutine factory).
# Single source of truth for BOTH entrypoints: deploy/run.py (the combined
# Docker runner) imports this list rather than keeping its own copies. It used
# to hold a full fork of every watcher body, and forks drift — x402_sweep ran
# only in main.py and never in production, then the recurring-payment executor
# was added to main.py and never reached the combined runner, so subscriptions
# silently never paid out there.
WATCHER_TASKS: tuple[tuple[str, Callable[[], Coroutine[Any, Any, None]]], ...] = (
    ('deposit', deposit_watcher),
    ('withdraw', withdraw_watcher),
    ('batch_withdraw', batch_withdraw_watcher),
    ('market', market_watcher),
    ('channel', channel_watcher),
    ('create2_sweep', create2_sweep_watcher),
    ('x402_sweep', x402_sweep_watcher),
    ('housekeeping', housekeeping_watcher),
    ('recurring', recurring_payment_executor),
    ('solvency', lambda: solvency_watcher(bot)),
    ('onchain', lambda: onchain_watcher(bot)),
    ('x402_reconcile', x402_reconcile_watcher),
    ('notification_outbox', notification_outbox_worker),
)


def watcher_done(name: str, task: asyncio.Task, stop: asyncio.Event | None) -> None:
    """Done-callback: surface a silent watcher death and request shutdown.

    A cancelled task is the normal shutdown path (not an error). A task that
    finished with an exception outside its own try/except, or returned early,
    means a background loop stopped running — log it loudly and set the stop
    event so the process exits and the supervisor/health check restarts it,
    instead of limping on with a dead money-path watcher.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        log.warning("watcher '%s' returned unexpectedly — stopping process", name)
    else:
        log.error(
            "watcher '%s' died unexpectedly (exception=%r) — stopping process",
            name, exc,
        )
    if stop is not None and not stop.is_set():
        stop.set()


def spawn_watchers(
    watchers: Iterable[tuple[str, Callable[[], Coroutine[Any, Any, None]]]],
    stop: asyncio.Event | None = None,
) -> list[asyncio.Task]:
    """Start each (name, factory) pair with the death→shutdown callback wired in.

    Both entrypoints go through here — main() passes WATCHER_TASKS, deploy/run.py
    passes the same list plus its gated agent — so neither can forget the callback
    the way deploy's forked copy of the watcher bodies did.
    """
    tasks: list[asyncio.Task] = []
    for name, watcher in watchers:
        task = asyncio.create_task(watcher())
        task.add_done_callback(lambda task=task, name=name: watcher_done(name, task, stop))
        tasks.append(task)
    return tasks

async def _run_webhook(stop: asyncio.Event | None=None) -> None:
    """Register the webhook with Telegram, serve the API, keep watchers alive.

    Single-process mode for hosts like Render: uvicorn serves web/server.py
    (which includes the /telegram-webhook endpoint) on $PORT while the
    deposit/withdraw/market watchers run as tasks in the same loop. Without a
    bound port the platform health check kills the service.
    """
    import os

    import uvicorn

    from web import hook
    from web.server import app as web_app
    await hook.bot.set_webhook(url=config.WEBHOOK_URL, secret_token=hook.webhook_secret())
    log.info('webhook registered: %s', config.WEBHOOK_URL)
    port = int(os.environ.get('PORT', str(config.WEB_PORT)))
    uvi = uvicorn.Server(uvicorn.Config(web_app, host=config.WEB_HOST, port=port, log_level='info'))
    server = asyncio.create_task(uvi.serve())
    wait = stop or asyncio.Event()
    stop_task = asyncio.create_task(wait.wait())
    try:
        await asyncio.wait([server, stop_task], return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop_task.cancel()
        server.cancel()
        await asyncio.gather(server, stop_task, return_exceptions=True)

# How long to wait before restarting polling after Telegram is unreachable.
# Shared with deploy/run.py so the two entrypoints back off identically.
RETRY_SECONDS = 15

# Canonical Telegram command menu. deploy/run.py reuses this list so the menu
# cannot diverge between entrypoints, and web/server.py publishes the same
# descriptions through /api/info — bot/commands_catalog.py holds the data.
BOT_COMMANDS = [BotCommand(command=c, description=d) for c, d in BOT_COMMAND_SPECS]

async def main() -> None:
    config.validate()
    log.info('hot wallet: %s', base.hot_wallet())
    from web.mini import public_base_url
    log.info('mini app url: %s', public_base_url() + '/app')
    dp = Dispatcher()
    dp.include_router(router)
    try:
        from .handlers import AI_BOT_COMMAND
        await bot.set_my_commands([AI_BOT_COMMAND, *BOT_COMMANDS])
    except Exception as e:
        log.warning('set_my_commands failed: %s', e)

    # One stop event drives every exit path: a signal, a watcher death
    # (watcher_done) or polling/webhook ending on its own. It used to be only
    # signals, and a watcher that died left this process polling with a broken
    # money path — deposits and payouts silently stopped while the bot answered.
    stop = asyncio.Event()
    tasks = spawn_watchers(WATCHER_TASKS, stop)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    try:
        while not stop.is_set():
            # handle_signals=False keeps our handlers authoritative: aiogram's
            # start_polling would otherwise replace them and the stop event would
            # never see a container SIGTERM.
            if config.WEBHOOK_URL:
                worker = asyncio.create_task(_run_webhook(stop))
            else:
                worker = asyncio.create_task(
                    dp.start_polling(bot, skip_updates=True, handle_signals=False)
                )
            waiter = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    {worker, waiter}, return_when=asyncio.FIRST_COMPLETED
                )
                if worker in done and not worker.cancelled():
                    exc = worker.exception()
                    if exc is None:
                        break  # polling or the webhook ended on its own
                    if isinstance(exc, TelegramNetworkError):
                        log.warning('telegram unreachable, retrying in %ds: %s', RETRY_SECONDS, exc)
                        await asyncio.sleep(RETRY_SECONDS)
                        continue
                    raise exc
            finally:
                for t in (worker, waiter):
                    if not t.done():
                        t.cancel()
                await asyncio.gather(worker, waiter, return_exceptions=True)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await ledger.close()
        except Exception:
            pass
        log.info('bot shut down gracefully')


if __name__ == '__main__':
    asyncio.run(main())
