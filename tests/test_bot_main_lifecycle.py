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
import re
import signal
from pathlib import Path

import pytest

from bot import main as bot_main

ROOT = Path(__file__).resolve().parents[1]


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


def _telegram_server_side_bases():
    """The aiogram classes that report a failure on Telegram's side of the wire.

    Read off the library's own hierarchy, not from a list kept here: `check_response`
    builds a `TelegramServerError` for any 5xx Telegram answers, and today that family
    is `TelegramServerError` plus its one child `RestartingTelegram` (Telegram saying
    it is restarting). A new 5xx class in a future aiogram joins the law by existing.
    """
    from aiogram.exceptions import TelegramNetworkError, TelegramServerError

    def descendants(cls):
        found = [cls]
        for sub in cls.__subclasses__():
            found.extend(descendants(sub))
        return found

    server_side = {TelegramNetworkError, *descendants(TelegramServerError)}
    assert len(server_side) >= 3, f"aiogram's 5xx family reads {sorted(server_side)} — the walker is blind"
    return server_side


def _telegram_permanent_classes():
    """The answers that say this process is wrong, which retrying cannot fix."""
    from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError

    def descendants(cls):
        found = [cls]
        for sub in cls.__subclasses__():
            found.extend(descendants(sub))
        return found

    permanent = {*descendants(TelegramUnauthorizedError), *descendants(TelegramBadRequest)}
    assert len(permanent) >= 2, f"aiogram's permanent-answer family reads {sorted(permanent)}"
    return permanent


def test_the_retry_rule_covers_every_server_side_answer_and_no_permanent_one():
    """One named tuple decides which Telegram failures are worth another try.

    The shipped rule named `TelegramNetworkError` alone, and that class does *not*
    contain `TelegramServerError`: aiogram builds the second from a 5xx status, as a
    sibling. So «Telegram server says - Bad Gateway» at `get_me` fell through
    `raise exc`, ended `_start_bot_polling`, and took the combined process — the web
    server and the money paths with it — down for the minutes until the supervisor
    started it again. `data/runtime/watchdog.log` measured that shape over the 270 h ending 2026-11-17:
    origin=False on 1,830 of 5,563 verdict passes and 707 origin starts.

    Coverage, because that is what `isinstance` asks: every server-side class has to
    sit under one of the tuple's members, and no permanent answer may.
    """
    for cls in _telegram_server_side_bases():
        assert issubclass(cls, bot_main.TRANSIENT_TELEGRAM_ERRORS), (
            f"{cls.__name__} is Telegram's to fix, and the retry rule does not cover it — "
            "a polling restart is what a 5xx asks for, not a dead process"
        )
    for cls in _telegram_permanent_classes():
        assert not issubclass(cls, bot_main.TRANSIENT_TELEGRAM_ERRORS), (
            f"{cls.__name__} is a permanent answer (a revoked token, a malformed call); "
            "retrying it forever hides the one report that says what broke"
        )
    assert len(bot_main.TRANSIENT_TELEGRAM_ERRORS) >= 2, (
        "the retry rule names one class again, which is how a Telegram 5xx killed the site"
    )


def test_no_retry_site_names_one_telegram_class_by_hand():
    """The retry decision is the tuple, everywhere, or it is the bug again.

    Three sites ask this question (`bot/main.py`'s polling supervisor and the two
    loops in `deploy/run.py`). A site that hand-names one class reads one family and
    re-raises the other, which is exactly what shipped until this wave. The scan is
    scoped to the functions that back off with `RETRY_SECONDS`, because that sleep is
    what makes a handler a *retry* decision: `bot/base.py:739` catches
    `TelegramBadRequest` where the call happens, and that is a normal refusal, not a
    retry rule. The count of tuple-using sites is floored so a scan that finds nothing
    cannot pass.
    """
    import ast

    shared_tuple = re.compile(r"\bTRANSIENT_TELEGRAM_ERRORS\b")
    class_name = re.compile(r"(?:Telegram\w*Error|RestartingTelegram)$")
    sources = sorted((ROOT / "bot").glob("*.py")) + sorted((ROOT / "deploy").glob("*.py"))
    assert sources, "no runtime sources to walk"
    hand_named: list[str] = []
    shared: list[str] = []
    for path in sources:
        text = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if "RETRY_SECONDS" not in (ast.get_source_segment(text, node) or ""):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.ExceptHandler) and inner.type is not None:
                    targets = inner.type.elts if isinstance(inner.type, ast.Tuple) else [inner.type]
                elif (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "isinstance"
                    and len(inner.args) >= 2
                ):
                    targets = [inner.args[1]]
                else:
                    continue
                for target in targets:
                    segment = ast.get_source_segment(text, target) or ""
                    if shared_tuple.search(segment):
                        shared.append(f"{path.name}:{inner.lineno}")
                    elif class_name.search(segment):
                        hand_named.append(f"{path.name}:{inner.lineno} {segment}")
    assert len(shared) >= 3, f"only {shared} retry decisions read the shared tuple — the reader is blind"
    assert not hand_named, f"these sites decide a Telegram retry over a single class: {hand_named}"


@pytest.mark.parametrize(
    "error_cls",
    [pytest.param(c, id=c.__name__) for c in _telegram_server_side_bases()],
)
def test_a_server_side_telegram_answer_is_retried_and_not_fatal(monkeypatch, error_cls):
    """A Telegram failure on Telegram's side restarts polling; it must not take the
    whole process (and the ledger close) down with it.

    Parametrized over the family the law above derives, so `RestartingTelegram` — a
    class this repo has never named — is measured too.
    """
    from aiogram import Dispatcher

    attempts = []

    async def _quiet():
        await asyncio.sleep(60)

    closed = _stub_entrypoint(monkeypatch, (("quiet", _quiet),))
    monkeypatch.setattr(bot_main, "RETRY_SECONDS", 0.01)

    async def _flaky(self, *bots, **kw):
        attempts.append(error_cls.__name__)
        if len(attempts) == 1:
            raise error_cls(message="telegram said no", method="getUpdates")
        await asyncio.sleep(60)

    monkeypatch.setattr(Dispatcher, "start_polling", _flaky)

    async def _scene():
        task = asyncio.create_task(bot_main.main())
        for _ in range(2000):
            if len(attempts) >= 2:
                break
            await asyncio.sleep(0.005)
        assert len(attempts) >= 2, f"{error_cls.__name__} was never retried — polling stayed dead"
        assert closed == [], f"{error_cls.__name__} tore the process down instead of retrying"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_scene())


def test_a_revoked_token_ends_the_process_instead_of_retrying_forever(monkeypatch):
    """401 is not Telegram's fault, and spinning on it keeps the site up while the
    bot answers nobody.

    The other half of the retry rule: without this, widening the transient set to
    «everything from aiogram» would pass every test above.
    """
    from aiogram import Dispatcher
    from aiogram.exceptions import TelegramUnauthorizedError

    attempts = []

    async def _quiet():
        await asyncio.sleep(60)

    closed = _stub_entrypoint(monkeypatch, (("quiet", _quiet),))
    monkeypatch.setattr(bot_main, "RETRY_SECONDS", 0.01)

    async def _refused(self, *bots, **kw):
        attempts.append(len(attempts))
        raise TelegramUnauthorizedError(message="token revoked", method="getUpdates")

    monkeypatch.setattr(Dispatcher, "start_polling", _refused)

    async def _scene():
        task = asyncio.create_task(bot_main.main())
        with pytest.raises(TelegramUnauthorizedError):
            await asyncio.wait_for(task, timeout=20)

    asyncio.run(_scene())
    assert len(attempts) == 1, f"a permanent answer retried {len(attempts)} times"
    assert closed == [True], "the process did not shut down over a permanent answer"


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
