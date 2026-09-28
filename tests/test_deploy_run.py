"""Regression tests for the combined deploy runner (deploy/run.py).

The critical invariants:
  1. The combined runner starts exactly bot.main's watcher set, taken from the
     same list object — the two entrypoints must never hold separate copies.
  2. A watcher that dies unexpectedly must surface loudly and stop the
     process (via the stop event) instead of the system limping on with a
     dead money-path; a cancelled watcher is the normal shutdown path and
     must NOT trigger that.
"""

import asyncio

from deploy import run as deploy_run


def test_deploy_reuses_bot_mains_watcher_list():
    """The runner takes bot.main's list itself, never a copy of it.

    deploy/run.py used to keep its own body for every watcher, and copies
    drift: x402_sweep ran only in main.py, and recurring_payment_executor was
    added to main.py later and never ran here at all, so subscriptions silently
    never paid out in the combined (Docker) entrypoint. Identity is what makes
    that class of bug impossible; equality would still let the two lists drift.
    """
    from bot import main as bot_main

    assert deploy_run._watcher_tasks() is bot_main.WATCHER_TASKS


def test_watcher_set_covers_every_background_loop():
    """The list must still hold the loops whose absence was a bug — an empty or
    trimmed set would satisfy the identity check just as easily."""
    from bot import main as bot_main

    assert {name for name, _ in bot_main.WATCHER_TASKS} == {
        "deposit",
        "withdraw",
        "batch_withdraw",
        "market",
        "channel",
        "create2_sweep",
        "x402_sweep",
        "housekeeping",
        "recurring",
        "solvency",
        "onchain",
        "x402_reconcile",
        "notification_outbox",
    }


def test_watchers_are_zero_arg_coroutine_factories():
    """Each entry must return a coroutine when called with no arguments — both
    bot.main and _spawn_watchers call `watcher()`, so a watcher that needed an
    argument would raise TypeError at startup instead of running."""
    from bot import main as bot_main

    for name, watcher in bot_main.WATCHER_TASKS:
        coro = watcher()
        assert asyncio.iscoroutine(coro), f"watcher '{name}' is not awaitable"
        coro.close()


def test_spawn_watchers_starts_every_entry_in_the_list(monkeypatch):
    """Tasks come from _watcher_tasks() at call time, so a watcher added to
    bot.main runs in the combined entrypoint with no edit here."""
    seen = []

    def _make(name):
        async def _watcher():
            seen.append(name)
            await asyncio.sleep(3600)

        return _watcher

    monkeypatch.setattr(
        deploy_run, "_watcher_tasks", lambda: (("a", _make("a")), ("b", _make("b")))
    )

    async def _scene():
        tasks = deploy_run._spawn_watchers(asyncio.Event())
        await asyncio.sleep(0.01)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(_scene())
    assert seen == ["a", "b"]


def test_spawn_watchers_wires_the_death_callback(monkeypatch):
    """The spawner, not each watcher, owns the death-→-shutdown callback: a
    watcher bot.main adds later gets it automatically."""
    async def _die():
        raise RuntimeError("boom")

    monkeypatch.setattr(deploy_run, "_watcher_tasks", lambda: (("die", _die),))
    stop = asyncio.Event()

    async def _scene():
        tasks = deploy_run._spawn_watchers(stop)
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(_scene())
    assert stop.is_set()


def test_signal_handlers_registered(monkeypatch):
    """_run_combined must register SIGTERM/SIGINT handlers — otherwise a
    container SIGTERM kills the process instead of allowing a graceful ledger
    close. The loop's add_signal_handler is stubbed per-instance so the test
    runs on Windows too (where the real method raises NotImplementedError)."""
    import signal

    registered = []

    async def _run():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", lambda sig, cb: registered.append(sig))

        async def _fake_bot(stop):
            assert stop is not None and not stop.is_set()

        async def _fake_web(stop):
            assert stop is not None
            return

        monkeypatch.setattr(deploy_run, "_start_bot_polling", _fake_bot)
        monkeypatch.setattr(deploy_run, "_start_web_server", _fake_web)
        # config.validate() may try RPC; stub it to a no-op, and the hot-wallet
        # print reads config.HOT_WALLET_KEY (module-level `config` import).
        from bot import config as _config

        monkeypatch.setattr(_config, "validate", lambda: None)
        monkeypatch.setattr(_config, "HOT_WALLET_KEY", None)
        # _run_combined imports public_base_url lazily; stub the real module.
        import types

        fake_mini = types.ModuleType("web.mini")
        fake_mini.public_base_url = lambda: "http://localhost"
        import sys

        monkeypatch.setitem(sys.modules, "web.mini", fake_mini)
        await deploy_run._run_combined()

    asyncio.run(_run())
    assert signal.SIGTERM in registered
    assert signal.SIGINT in registered
