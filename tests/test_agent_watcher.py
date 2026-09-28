"""Tests for the autonomous-agent wiring in deploy/run.py.

Invariants:
  - The agent starts ONLY when AGENT_TG_ID > 0 AND its config validates
    (fail-closed: a misconfigured agent must never run with real money).
  - The agent is NOT part of bot.main.WATCHER_TASKS (it runs nowhere else, and
    it is gated, so it can never be a plain entry in that list).
  - _agent_watcher must be an awaitable wired to the same stop-event
    semantics as every other watcher.
"""

import asyncio

from bot import main as bot_main
from deploy import run as deploy_run


def _patch_agent_config(monkeypatch, tg_id, errors):
    from agent import config as agent_config
    monkeypatch.setattr(agent_config, "AGENT_TG_ID", tg_id)
    monkeypatch.setattr(agent_config, "validate", lambda: errors)


def test_agent_disabled_without_tg_id(monkeypatch):
    _patch_agent_config(monkeypatch, tg_id=0, errors=[])
    assert deploy_run._agent_enabled() is False


def test_agent_disabled_on_invalid_config(monkeypatch):
    _patch_agent_config(monkeypatch, tg_id=123, errors=["AGENT_DAILY_CAP must be > 0"])
    assert deploy_run._agent_enabled() is False


def test_agent_enabled_with_valid_config(monkeypatch):
    _patch_agent_config(monkeypatch, tg_id=123, errors=[])
    assert deploy_run._agent_enabled() is True


def test_agent_enabled_never_raises(monkeypatch):
    from agent import config as agent_config

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_config, "AGENT_TG_ID", 123)
    monkeypatch.setattr(agent_config, "validate", _boom)
    assert deploy_run._agent_enabled() is False


def test_agent_not_in_watchers_set():
    """bot.main's watcher list is started unconditionally by _spawn_watchers(); the
    agent is gated (AGENT_TG_ID > 0 + valid caps), so an entry here would run a
    misconfigured agent with real money on every deploy."""
    from bot import main as bot_main

    assert "agent" not in {name for name, _ in deploy_run._watcher_tasks()}
    assert deploy_run._watcher_tasks() is bot_main.WATCHER_TASKS


def test_agent_watcher_is_awaitable():
    coro = deploy_run._agent_watcher(None, None)  # type: ignore[arg-type]
    assert asyncio.iscoroutine(coro)
    coro.close()


def test_agent_watcher_death_sets_stop(monkeypatch):
    """Same contract as other watchers: a silent agent death must stop the
    process — simulate by monkeypatching run_loop to raise immediately. The
    callback comes from bot.main.spawn_watchers, the same helper the watchers
    use, so there is no second copy of the wiring to drift."""
    import agent.main as agent_main

    async def _die(stop=None):
        raise RuntimeError("agent died")

    monkeypatch.setattr(agent_main, "run_loop", _die)
    _patch_agent_config(monkeypatch, tg_id=123, errors=[])

    stop = asyncio.Event()

    async def _scene():
        tasks = bot_main.spawn_watchers(
            [("agent", lambda: deploy_run._agent_watcher(None, None, stop))], stop
        )
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(_scene())
    assert stop.is_set()
