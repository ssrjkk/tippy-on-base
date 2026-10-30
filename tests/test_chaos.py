"""Chaos tests — verify exception handling in critical paths.

Pure unit tests: no DB, no RPC, no network.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRPCChaos:
    """Simulate RPC failures against the real failover paths."""

    @staticmethod
    def _provider(*, exc=None, logs=()):
        import types

        def get_logs(_params):
            if exc:
                raise exc
            return list(logs)

        return types.SimpleNamespace(eth=types.SimpleNamespace(get_logs=get_logs))

    def test_scan_deposits_fails_over_to_the_next_provider(self, monkeypatch):
        """A dead primary must not drop deposits: the scan has to walk the
        fallback list before giving up."""
        from bot import base

        seen = []

        def fake_web3(_provider_obj):
            seen.append(_provider_obj)
            return self._provider(logs=[])

        monkeypatch.setattr(base, "w3", self._provider(exc=ConnectionError("primary down")))
        monkeypatch.setattr(base, "_RPC_FALLBACKS", ["http://fallback.invalid"])
        monkeypatch.setattr(base, "Web3", types_web3_factory(fake_web3))

        assert base._scan_deposits(10, 20) == []
        assert seen == ["http://fallback.invalid"]

    def test_scan_deposits_raises_when_every_provider_is_down(self, monkeypatch):
        from bot import base

        monkeypatch.setattr(base, "w3", self._provider(exc=ConnectionError("primary down")))
        monkeypatch.setattr(base, "_RPC_FALLBACKS", [])

        with pytest.raises(RuntimeError, match="all RPC providers failed"):
            base._scan_deposits(10, 20)

    @pytest.mark.asyncio
    async def test_hot_balance_raises_instead_of_reporting_zero(self, monkeypatch):
        """A blind RPC must surface as an error — a returned 0.0 would read as
        «reserves empty» and either hide insolvency or fake it."""
        from bot import base

        class _DeadContract:
            def __getattr__(self, _name):
                raise ConnectionError("RPC down")

        monkeypatch.setattr(base, "usdc", _DeadContract())
        monkeypatch.setattr(base, "_w3_providers", [])

        with pytest.raises(RuntimeError, match="all RPC providers failed"):
            await base.hot_balance()


def types_web3_factory(build):
    """Stub for `bot.base.Web3` used to build fallback providers: the code calls
    Web3(Web3.HTTPProvider(url, ...)), so the name must be callable and carry
    HTTPProvider."""
    class _FakeWeb3:
        HTTPProvider = staticmethod(lambda url, **_kw: url)

        def __call__(self, endpoint):
            return build(endpoint)

    return _FakeWeb3()


class TestSolvencyChaos:
    """Test solvency monitoring under degraded conditions."""

    def test_alert_cooldown_is_minimum_5min(self):
        from bot.solvency import _ALERT_COOLDOWN
        assert _ALERT_COOLDOWN >= 300

    @pytest.mark.asyncio
    async def test_solvency_check_handles_ledger_failure(self):
        """_check_solvency doesn't crash when ledger throws."""
        from bot.solvency import _check_solvency

        mock_bot = AsyncMock()
        mock_ledger = MagicMock()
        mock_ledger.total_liabilities = AsyncMock(side_effect=Exception("DB down"))

        with patch("bot.solvency.ledger", mock_ledger):
            # Should not raise — _check_solvency catches internally
            await _check_solvency(mock_bot)
            # Bot's send_message should NOT be called (no alert on partial failure)
            mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_insolvency_alert_fires_once_per_cooldown(self, monkeypatch):
        """The canary must actually alert on a real deficit, and must not flood
        the operator: a second check inside the window stays silent."""
        from bot import solvency

        monkeypatch.setattr(solvency, "_last_alert_ts", 0.0)
        monkeypatch.setattr(solvency.config, "VAULT_ADDRESS", "")
        monkeypatch.setattr(solvency.config, "SOLVENCY_ALERT_CHAT_ID", 424242)
        monkeypatch.setattr(solvency.ledger, "total_liabilities", AsyncMock(return_value=1_000_000_000))
        monkeypatch.setattr(solvency.ledger, "x402_unswept_credit_total", AsyncMock(return_value=0))
        monkeypatch.setattr(solvency.base, "hot_balance", AsyncMock(return_value=1.0))
        monkeypatch.setattr(solvency.base, "receive_pool_balance", AsyncMock(return_value=0.0))

        bot = AsyncMock()
        await solvency._check_solvency(bot)
        assert bot.send_message.await_count == 1
        assert "INSOLVENCY" in bot.send_message.await_args.args[1]

        await solvency._check_solvency(bot)
        assert bot.send_message.await_count == 1  # cooldown holds

        monkeypatch.setattr(solvency, "_last_alert_ts", 0.0)
        await solvency._check_solvency(bot)
        assert bot.send_message.await_count == 2


class TestHandlerErrorsChaos:
    """The global @router.errors() safety net."""

    @pytest.mark.asyncio
    async def test_private_chat_error_informs_user(self, monkeypatch):
        from unittest.mock import AsyncMock

        from aiogram.types import Chat, ErrorEvent, Message, Update, User

        from bot.handlers import _common

        chat = Chat(id=42, type="private")
        user = User(id=7, is_bot=False, first_name="T")
        msg = Message(message_id=1, date=0, text="/boom", chat=chat, from_user=user)
        up = Update(update_id=1, message=msg)
        bot = AsyncMock()
        up._bot = bot

        monkeypatch.setattr(_common, "user_lang", AsyncMock(return_value="ru"))
        monkeypatch.setattr(_common.config, "ADMIN_TG_ID", "")

        await _common._on_error(ErrorEvent(update=up, exception=ValueError("boom")))

        assert bot.send_message.await_count == 1
        chat_id, text = bot.send_message.await_args.args
        assert chat_id == 42
        assert text == _common.i18n.t("ru", "error_generic")

    @pytest.mark.asyncio
    async def test_error_handling_never_raises(self, monkeypatch):
        from unittest.mock import AsyncMock

        from aiogram.types import ErrorEvent, Update

        from bot.handlers import _common

        monkeypatch.setattr(_common, "user_lang", AsyncMock(return_value="ru"))
        monkeypatch.setattr(_common.config, "ADMIN_TG_ID", "")

        # Update with nothing inside — the handler must not raise.
        up = Update(update_id=1)
        up._bot = AsyncMock()
        await _common._on_error(ErrorEvent(update=up, exception=RuntimeError("x")))
        assert up.bot.send_message.await_count == 0  # no user chat -> nothing sent


class TestCreate2Chaos:
    """Test CREATE2 address derivation."""

    def test_disabled_returns_empty(self):
        """When factory not set, returns empty."""
        from bot.create2 import get_deposit_address, is_create2_enabled

        with patch("bot.create2.FACTORY_ADDRESS", ""):
            assert not is_create2_enabled()
            assert get_deposit_address(12345) == ""

    def test_deterministic(self):
        """Same tg_id always produces same address."""
        from bot.create2 import _compute_address

        with patch("bot.create2.FACTORY_ADDRESS", "0x1234567890abcdef1234567890abcdef12345678"):
            a1 = _compute_address(99999)
            a2 = _compute_address(99999)
            assert a1 == a2
            assert a1.startswith("0x")
            assert len(a1) == 42

    def test_different_users_different_addresses(self):
        """Different tg_ids produce different addresses."""
        from bot.create2 import _compute_address

        with patch("bot.create2.FACTORY_ADDRESS", "0x1234567890abcdef1234567890abcdef12345678"):
            a1 = _compute_address(111)
            a2 = _compute_address(222)
            assert a1 != a2


class TestMetricsChaos:
    """Test metrics collection under degraded conditions."""

    @pytest.mark.asyncio
    async def test_collect_metrics_survives_rpc_failure(self):
        """Metrics endpoint returns partial data when RPC fails."""
        from web.metrics import collect_metrics

        mock_ledger = MagicMock()
        mock_ledger.total_liabilities = AsyncMock(side_effect=Exception("DB down"))
        mock_ledger.global_stats = AsyncMock(side_effect=Exception("DB down"))
        mock_ledger.open_markets = AsyncMock(return_value=[])

        mock_base = MagicMock()
        mock_base.vault_balance = AsyncMock(side_effect=Exception("RPC down"))
        mock_base.hot_balance = AsyncMock(side_effect=Exception("RPC down"))

        with patch("web.metrics.ledger", mock_ledger), \
             patch("web.metrics.base", mock_base), \
             patch("web.metrics.config") as mock_config:
            mock_config.VAULT_ADDRESS = ""
            mock_config.USDC_DECIMALS = 6

            result = await collect_metrics()
            assert "tipbot_solvent" in result
            assert "tipbot_liabilities_usdc" in result
            assert "tipbot_reserves_usdc" in result
