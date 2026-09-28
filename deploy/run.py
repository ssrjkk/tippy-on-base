"""Combined runner: Telegram bot (polling) + web server (FastAPI) in one process.

The bot needs the web server for:
  - Mini App (Telegram WebApp)
  - Public dashboard (/)
  - x402 endpoints for AI agents
  - Health checks

Made by @ssrjkk — github.com/ssrjkk.

Usage:
    python run.py                    # bot + web server on WEB_PORT
    python run.py --web-only         # web server only (no Telegram)
    python run.py --bot-only         # bot only (legacy polling mode)
"""
import argparse
import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot import config

if os.environ.get("LOG_FORMAT") == "json":
    from pythonjsonlogger.json import JsonFormatter
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter("%%(asctime)s %%(levelname)s %%(name)s %%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

log = logging.getLogger("tipbot")


async def _start_web_server(stop: asyncio.Event | None = None) -> None:
    """Start uvicorn serving the FastAPI app."""
    import uvicorn

    from web.server import app as web_app

    port = int(os.environ.get("PORT", str(config.WEB_PORT)))
    uvi = uvicorn.Server(uvicorn.Config(
        web_app,
        host=config.WEB_HOST,
        port=port,
        log_level="info",
        # Never let uvicorn rewrite request.client from X-Forwarded-For /
        # X-Forwarded-Proto before app-level checks: uvicorn trusts those
        # headers on forwarded_allow_ips peers (e.g. a local cloudflared) and
        # would pre-inject a client-spoofed identity, defeating the app's
        # TRUSTED_PROXY_PEERS guard. real IP resolution is app-owned.
        proxy_headers=False,
    ))
    # If a stop Event is provided, wait on it so a graceful shutdown signal can
    # ask uvicorn to exit instead of the process being SIGKILLed mid-request.
    if stop is not None:
        waiter = asyncio.create_task(stop.wait())
        serve = asyncio.create_task(uvi.serve())
        try:
            await asyncio.wait([serve, waiter], return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
            if not serve.done():
                uvi.should_exit = True
                await asyncio.gather(serve, waiter, return_exceptions=True)
        return
    await uvi.serve()


# The bot-side watchers come from bot.main.WATCHER_TASKS — see _watcher_tasks().


def _watcher_tasks() -> tuple[tuple[str, Callable[[], Coroutine[Any, Any, None]]], ...]:
    """The watcher set to run, taken straight from bot.main.

    This runner used to carry its own copy of every watcher body, and the copy
    drifted: deposit/market notifications bypassed the notification outbox (no
    retry when Telegram is busy) and read a user's settings twice per deposit,
    the deposit poll lost main.py's escalating backoff, and
    recurring_payment_executor — added to main.py later — never ran here at all,
    so subscriptions silently never paid out in the combined (Docker) entrypoint.
    Importing main.py's Bot too is not incidental: that is where the
    TELEGRAM_API_PROXY session is built (bot/telegram_transport.py), and a
    second Bot() constructed here meant the documented proxy option was ignored
    and every Telegram call went direct.

    Imported lazily so `--web-only` does not construct it.
    """
    from bot import main as bot_main

    return bot_main.WATCHER_TASKS


def _spawn_watchers(stop: asyncio.Event | None = None) -> list[asyncio.Task]:
    """Start every bot.main watcher with the death-→-shutdown callback.

    The wiring lives in bot.main.spawn_watchers: both entrypoints share one
    implementation, so neither can quietly drop the callback the way this
    runner's forked watcher bodies used to.
    """
    from bot import main as bot_main

    return bot_main.spawn_watchers(_watcher_tasks(), stop)


def _agent_enabled() -> bool:
    """True when the autonomous agent should run in this process.

    Gated on AGENT_TG_ID > 0 (a real, funded bot user) AND a valid caps set.
    A misconfigured agent (zero caps, per-tx > daily) must never start: the
    agent refuses to run on such a set anyway (fail-closed).
    """
    try:
        from agent import config as agent_config
        return agent_config.AGENT_TG_ID > 0 and not agent_config.validate()
    except Exception:
        return False


async def _agent_watcher(bot, ledger, stop: asyncio.Event | None = None) -> None:
    """Autonomous agent loop (news → LLM → markets/bets → EAS attestations).

    Runs only when `_agent_enabled()`; the caller is responsible for gating
    (an early return here would look like a watcher death and stop the whole
    process via the done-callback).
    """
    from agent.main import run_loop

    log.info("autonomous agent watcher starting")
    await run_loop(stop)


async def _start_bot_polling(stop: asyncio.Event | None = None) -> None:
    """Start aiogram polling + watchers.

    Watcher tasks are created with a done-callback so a silent death (a
    watcher that raises outside its own try/except, or finishes its loop
    unexpectedly) is logged instead of being invisible, and cancels the whole
    process via the stop event so the supervisor/health check can restart it.
    """
    from aiogram import Dispatcher
    from aiogram.exceptions import TelegramNetworkError

    from bot import main as bot_main
    from bot.handlers import router
    from bot.ledger import async_ledger as ledger

    # main.py's Bot instance rather than a new one: it carries the
    # TELEGRAM_API_PROXY / pinned-IP session, and the handlers, web hooks and
    # watchers all reach Telegram through that same object.
    tg_bot = bot_main.bot
    dp = Dispatcher()
    dp.include_router(router)

    try:
        from bot.handlers import AI_BOT_COMMAND
        # Single source of truth for the command menu: bot.main.BOT_COMMANDS.
        await tg_bot.set_my_commands([AI_BOT_COMMAND, *bot_main.BOT_COMMANDS])
    except Exception as e:
        log.warning("set_my_commands failed: %s", e)

    tasks: list[asyncio.Task] = _spawn_watchers(stop)

    # Autonomous agent — optional, gated on AGENT_TG_ID > 0 + valid caps.
    # Not part of bot.main.WATCHER_TASKS (the agent runs nowhere else); it goes
    # through the same spawn helper, so a silent agent death stops the process
    # exactly like a dead watcher does.
    if _agent_enabled():
        tasks += bot_main.spawn_watchers(
            [("agent", lambda: _agent_watcher(tg_bot, ledger, stop))], stop
        )

    log.info("bot polling starting")
    try:
        if stop is not None:
            waiter = asyncio.create_task(stop.wait())
            try:
                while not stop.is_set():
                    # handle_signals=False: aiogram would replace the SIGTERM/SIGINT
                    # handlers _run_combined registered, so the stop event — the one
                    # thing that also shuts the web server down — never got set.
                    poll = asyncio.create_task(
                        dp.start_polling(tg_bot, skip_updates=True, handle_signals=False)
                    )
                    try:
                        done, _ = await asyncio.wait([poll, waiter], return_when=asyncio.FIRST_COMPLETED)
                        if poll in done:
                            exc = poll.exception()
                            if exc is not None:
                                if isinstance(exc, TelegramNetworkError):
                                    log.warning("telegram unreachable, retrying in %ds: %s",
                                                bot_main.RETRY_SECONDS, exc)
                                    await asyncio.sleep(bot_main.RETRY_SECONDS)
                                    continue
                                raise exc
                            # Polling ended normally (e.g. stop set elsewhere).
                            break
                    finally:
                        if not poll.done():
                            poll.cancel()
                            await asyncio.gather(poll, return_exceptions=True)
            finally:
                waiter.cancel()
        else:
            while True:
                try:
                    await dp.start_polling(tg_bot, skip_updates=True, handle_signals=False)
                    break
                except TelegramNetworkError as e:
                    log.warning("telegram unreachable, retrying in %ds: %s",
                                bot_main.RETRY_SECONDS, e)
                    await asyncio.sleep(bot_main.RETRY_SECONDS)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await ledger.close()
        except Exception:
            log.warning("ledger close failed", exc_info=True)


async def _run_combined() -> None:
    """Run bot polling + web server concurrently in one process."""
    log.info("=== COMBINED MODE: bot + web server ===")
    try:
        from web3 import Web3
        addr = Web3().eth.account.from_key(config.HOT_WALLET_KEY).address if config.HOT_WALLET_KEY else None
    except Exception:
        addr = None
    log.info("hot wallet configured: %s", addr if addr else "MISSING")

    from web.mini import public_base_url
    log.info("mini app url: %s/app", public_base_url())

    config.validate()

    stop = asyncio.Event()

    # Graceful shutdown: SIGTERM/SIGINT set the stop Event instead of killing
    # the process mid-write, so in-flight watchers (and the ledger) can finish
    # and close cleanly. uvicorn reads the same Event to exit its loop.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
            log.info("registered %s for graceful shutdown", sig.name)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    try:
        await asyncio.gather(
            _start_bot_polling(stop),
            _start_web_server(stop),
        )
    finally:
        if not stop.is_set():
            stop.set()
        log.info("combined runner stopped")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--web-only", action="store_true", help="Web server only (no Telegram bot)")
    ap.add_argument("--bot-only", action="store_true", help="Bot only (legacy polling, no web)")
    args = ap.parse_args()

    config.validate()

    if args.web_only:
        log.info("WEB-ONLY mode")
        asyncio.run(_run_web_only())
    elif args.bot_only:
        log.info("BOT-ONLY mode")
        asyncio.run(_run_bot_only())
    else:
        asyncio.run(_run_combined())


async def _run_web_only() -> None:
    """Web server with graceful shutdown."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await _start_web_server(stop)


async def _run_bot_only() -> None:
    """Bot polling with graceful shutdown + ledger close."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await _start_bot_polling(stop)


if __name__ == "__main__":
    main()
