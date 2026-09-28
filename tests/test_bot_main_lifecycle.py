"""Tests for bot.main's process lifecycle.

Invariants:
  - Every watcher this process starts is wired to the death→shutdown callback,
    so a watcher that raises outside its own try/except (or returns early) ends
    the process instead of leaving it polling with a dead money path.
  - SIGTERM/SIGINT go through the same stop event, so a container stop still
    cancels the watchers and closes the ledger.
  - The callback is one implementation shared with deploy/run.py: a second copy
    is what drifted when the combined runner forked the watcher bodies.
"""

import asyncio
import signal

from bot import main as bot_main


def _watcher_done():
    from bot.main import watcher_done

    return watcher_done


def test_watcher_done_callback_sets_stop_on_death():
    """A watcher that dies with an exception must flag the stop event."""
    stop = asyncio.Event()

    async def _die():
        raise RuntimeError("boom")

    async def _scene():
        task = asyncio.create_task(_die())
        task.add_done_callback(lambda t: _watcher_done()("test_die", t, stop))
        await asyncio.sleep(0.01)

    asyncio.run(_scene())
    assert stop.is_set()


def test_watcher_done_callback_sets_stop_on_early_return():
    """A watcher that returns normally (no exception) is still a death: the
    background loop stopped running and must trigger shutdown."""
    stop = asyncio.Event()

    async def _return():
        return None

    async def _scene():
        task = asyncio.create_task(_return())
        task.add_done_callback(lambda t: _watcher_done()("test_return", t, stop))
        await asyncio.sleep(0.01)

    asyncio.run(_scene())
    assert stop.is_set()


def test_watcher_done_callback_ignores_cancellation():
    """A cancelled watcher is the graceful-shutdown path and must NOT stop the
    process (the entrypoint's own finally block already cancelled them)."""
    stop = asyncio.Event()
    started = asyncio.Event()

    async def _sleep():
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            pass

    async def _scene():
        task = asyncio.create_task(_sleep())
        await started.wait()
        task.cancel()
        task.add_done_callback(lambda t: _watcher_done()("test_cancel", t, stop))
        await asyncio.sleep(0.01)

    asyncio.run(_scene())
    assert not stop.is_set()


def test_spawn_watchers_starts_every_entry_and_wires_the_callback():
    """The helper, not the caller, owns the callback: an entrypoint that forgot
    to add it is exactly how a dead watcher went unnoticed."""
    started = []
    stop = asyncio.Event()

    def _make(name):
        async def _watcher():
            started.append(name)
            raise RuntimeError(name)

        return name, _watcher

    async def _scene():
        tasks = bot_main.spawn_watchers([_make("a"), _make("b")], stop)
        await asyncio.gather(*tasks, return_exceptions=True)
        return tasks

    tasks = asyncio.run(_scene())
    assert started == ["a", "b"]
    assert len(tasks) == 2
    assert stop.is_set()


def _stub_entrypoint(monkeypatch, watchers, polling_hangs=True):
    """Patch main() down to its lifecycle logic: no config, no RPC, no Telegram."""
    from aiogram import Dispatcher, Router

    from bot import config as bot_config

    monkeypatch.setattr(bot_config, "validate", lambda: None)
    monkeypatch.setattr(bot_config, "WEBHOOK_URL", "")
    monkeypatch.setattr(bot_main.base, "hot_wallet", lambda: "0xhotwallet")
    monkeypatch.setattr(bot_main, "WATCHER_TASKS", watchers)
    # The real router is a module singleton and aiogram refuses a second
    # attachment, which would limit main() to one call per process.
    monkeypatch.setattr(bot_main, "router", Router())

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(bot_main.bot, "set_my_commands", _noop)

    closed = []

    async def _close():
        closed.append(True)

    monkeypatch.setattr(bot_main.ledger, "close", _close)

    async def _start_polling(self, *bots, **kw):
        if polling_hangs:
            await asyncio.sleep(60)

    monkeypatch.setattr(Dispatcher, "start_polling", _start_polling)

    import sys
    import types

    fake_mini = types.ModuleType("web.mini")
    fake_mini.public_base_url = lambda: "http://localhost"
    monkeypatch.setitem(sys.modules, "web.mini", fake_mini)
    return closed


def test_main_exits_when_a_watcher_dies(monkeypatch):
    """The gap this closes: polling kept running after a watcher died, so the bot
    answered /tip while deposits, payouts or subscriptions were no longer
    executing — and no supervisor would restart a process that never exited."""

    async def _die():
        raise RuntimeError("deposit watcher died")

    async def _quiet():
        await asyncio.sleep(60)

    closed = _stub_entrypoint(monkeypatch, (("die", _die), ("quiet", _quiet)))
    asyncio.run(asyncio.wait_for(bot_main.main(), timeout=10))
    assert closed == [True], "the ledger was never closed on the death path"


def test_sigterm_stops_the_process_gracefully(monkeypatch):
    """A container SIGTERM must reach the same stop event: cancel the watchers,
    stop polling, close the ledger — not kill the process mid-write."""
    registered = {}

    def _add_signal_handler(sig, callback, *args):
        registered[sig] = callback

    async def _quiet():
        await asyncio.sleep(60)

    closed = _stub_entrypoint(monkeypatch, (("quiet", _quiet),))

    async def _scene():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", _add_signal_handler)
        task = asyncio.create_task(bot_main.main())
        for _ in range(500):
            if signal.SIGTERM in registered:
                break
            await asyncio.sleep(0.005)
        assert signal.SIGTERM in registered, "no graceful SIGTERM handler"
        assert signal.SIGINT in registered, "no graceful SIGINT handler"
        registered[signal.SIGTERM]()
        await asyncio.wait_for(task, timeout=10)

    asyncio.run(_scene())
    assert closed == [True]


def test_polling_error_is_retried_not_fatal(monkeypatch):
    """A Telegram network blip restarts polling; it must not take the whole
    process (and the ledger close) down with it."""
    from aiogram import Dispatcher
    from aiogram.exceptions import TelegramNetworkError

    attempts = []

    async def _quiet():
        await asyncio.sleep(60)

    closed = _stub_entrypoint(monkeypatch, (("quiet", _quiet),))
    monkeypatch.setattr(bot_main, "RETRY_SECONDS", 0.01)

    async def _flaky(self, *bots, **kw):
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise TelegramNetworkError(message="unreachable", method="getUpdates")
        await asyncio.sleep(60)

    monkeypatch.setattr(Dispatcher, "start_polling", _flaky)

    async def _scene():
        task = asyncio.create_task(bot_main.main())
        for _ in range(2000):
            if len(attempts) >= 2:
                break
            await asyncio.sleep(0.005)
        assert len(attempts) >= 2, "polling was never restarted after the network error"
        assert closed == [], "a network blip tore the process down instead of retrying"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_scene())


def test_main_passes_its_stop_event_to_webhook_mode(monkeypatch):
    """_run_webhook takes the stop event; main() must hand it the real one, or a
    watcher death in webhook mode leaves uvicorn serving with dead watchers."""
    from bot import config as bot_config

    seen = {}

    async def _fake_webhook(stop=None):
        seen["stop"] = stop
        await asyncio.sleep(60)

    async def _die():
        raise RuntimeError("webhook mode death")

    monkeypatch.setattr(bot_main, "_run_webhook", _fake_webhook)
    _stub_entrypoint(monkeypatch, (("die", _die),))
    monkeypatch.setattr(bot_config, "WEBHOOK_URL", "https://tipbot.example.com/tg")

    asyncio.run(asyncio.wait_for(bot_main.main(), timeout=10))
    assert seen["stop"] is not None
    assert seen["stop"].is_set()
