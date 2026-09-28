"""Tests for the agent package — caps, tools, news, decision, EAS.

Runs against the test database like the other test files.
"""

import json
import logging
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# caps.py
# ---------------------------------------------------------------------------

class TestCaps:
    def setup_method(self):
        self.state_file = Path("agent/.agent_state.json")
        if self.state_file.exists():
            self.state_file.unlink()

    def test_check_action_within_limits(self):
        from agent.caps import check_action
        result = check_action(1.0)
        assert result is None  # allowed

    def test_check_action_daily_cap(self):
        from agent.caps import _save_state, check_action
        _save_state({
            "daily_spent": 49.0,
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 0,
            "hour_ts": 0,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        result = check_action(2.0)  # would exceed $50
        assert result is not None
        assert "Daily cap" in result

    def test_check_action_per_tx_cap(self):
        from agent.caps import check_action
        result = check_action(100.0)  # > $10 per-tx
        assert result is not None
        assert "Per-tx cap" in result

    def test_check_action_rate_limit(self):
        from agent.caps import _save_state, check_action
        _save_state({
            "daily_spent": 0.0,
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 20,
            "hour_ts": int(time.time()) // 3600,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        result = check_action(1.0)
        assert result is not None
        assert "Rate limit" in result

    def test_check_action_reserves_budget(self):
        from agent.caps import _load_state, check_action
        result = check_action(5.0)
        assert result is None  # allowed + reserved
        state = _load_state()
        assert state["daily_spent_micro"] == 5_000_000  # $5 in micro-units
        assert state["actions_this_hour"] == 1
        assert state["consecutive_errors"] == 0

    def test_check_action_reservation_blocks_overshoot(self):
        from agent.caps import _load_state, _save_state, check_action
        # Two concurrent actions would both see the pre-reservation snapshot in
        # the old API; the reserve-on-check closes that TOCTOU.
        _save_state({
            "daily_spent_micro": 46_000_000,  # $46 in micro-units
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 0,
            "hour_ts": 0,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        assert check_action(4.0) is None       # reserves 4 (46+4=50)
        state = _load_state()
        assert state["daily_spent_micro"] == 50_000_000  # $50 in micro-units
        # The next check sees the reservation and must reject an overshoot.
        r2 = check_action(4.0)
        assert r2 is not None
        assert "Daily cap" in r2

    def test_release_action_returns_reserved_budget(self):
        from agent.caps import _load_state, check_action, release_action
        assert check_action(5.0) is None
        release_action(5.0)
        state = _load_state()
        assert state["daily_spent_micro"] == 0
        assert state["actions_this_hour"] == 0

    def test_record_error_triggers_breaker(self):
        from agent.caps import _load_state, record_error
        for _ in range(3):
            record_error()
        state = _load_state()
        assert state["cooldown_until"] > time.time()

    def test_get_status(self):
        from agent.caps import get_status
        s = get_status()
        assert "daily_spent_usdc" in s
        assert "cooldown_active" in s

    def test_daily_reset_logs_audit_trail(self, caplog):
        """Daily cap reset must log the previous spend for audit trail."""
        import logging

        from agent.caps import _save_state, check_action

        # Set state to yesterday with some spend
        yesterday = (__import__("time").strftime(
            "%Y-%m-%d",
            __import__("time").gmtime(__import__("time").time() - 86400)
        ))
        _save_state({
            "daily_spent_micro": 25_000_000,  # $25 spent
            "daily_date": yesterday,
            "actions_this_hour": 0,
            "hour_ts": 0,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })

        # Trigger a check — should reset and log
        with caplog.at_level(logging.INFO):
            result = check_action(1.0)

        assert result is None  # action allowed
        assert any("daily cap reset" in record.message for record in caplog.records)
        assert any("$25.00" in record.message for record in caplog.records)

    def teardown_method(self):
        if self.state_file.exists():
            self.state_file.unlink()


# ---------------------------------------------------------------------------
# news.py
# ---------------------------------------------------------------------------

class TestNews:
    def test_score_relevance(self):
        from agent.news import _score_relevance
        high = _score_relevance("Bitcoin ETF approved by SEC", "")
        low = _score_relevance("Free airdrop giveaway moon 100x", "")
        assert high > low

    def test_news_item_to_prompt(self):
        from agent.news import NewsItem
        item = NewsItem(
            title="ETH hits $5000",
            link="https://example.com",
            published="",
            source="test",
            relevance=0.8,
        )
        prompt = item.to_prompt()
        assert "<untrusted_news_item>" in prompt
        assert "ETH hits $5000" in prompt
        assert "</untrusted_news_item>" in prompt

    def test_dedup(self):
        from agent.news import _load_seen, _save_seen
        seen = _load_seen()
        initial_len = len(seen)
        seen.add("test_hash_123")
        _save_seen(seen)
        seen2 = _load_seen()
        assert "test_hash_123" in seen2
        # Cleanup
        seen2.discard("test_hash_123")
        _save_seen(seen2)


# ---------------------------------------------------------------------------
# tools.py (mocked ledger)
# ---------------------------------------------------------------------------

class TestTools:
    @pytest.mark.asyncio
    async def test_create_market_blocked_by_cap(self, ledger):
        # `ledger` rebinds agent.tools/agent.pnl to the hermetic test DB: the
        # stop-loss gate queries positions and balance before caps are checked.
        from agent.caps import _save_state
        _save_state({
            "daily_spent_micro": 49_000_000,  # $49 in micro-units
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 0,
            "hour_ts": 0,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        from agent.tools import create_market
        result = await create_market("Test?", ["Yes", "No"], hours=24, subsidy_usdc=2.0)
        assert "Daily cap" in result["error"]
        # Cleanup
        Path("agent/.agent_state.json").unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_place_bet_blocked_by_cap(self, ledger):
        from agent.caps import _save_state
        _save_state({
            "daily_spent_micro": 0,
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 0,
            "hour_ts": 0,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        from agent.tools import place_bet
        result = await place_bet(1, 0, 100.0)  # > $10 per-tx
        assert "Per-tx cap" in result["error"]
        Path("agent/.agent_state.json").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# signals.py
# ---------------------------------------------------------------------------

class TestSignals:
    @pytest.mark.asyncio
    async def test_sell_signal_blocked_by_cap(self):
        from agent.caps import _save_state
        _save_state({
            "daily_spent": 0.0,
            "daily_date": __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime()),
            "actions_this_hour": 20,
            "hour_ts": int(time.time()) // 3600,
            "consecutive_errors": 0,
            "cooldown_until": 0.0,
        })
        from agent.signals import sell_signal
        result = await sell_signal(1, "analysis", price_usdc=1.0)
        assert "error" in result
        Path("agent/.agent_state.json").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# decision.py (mocked LLM)
# ---------------------------------------------------------------------------

class TestDecision:
    def test_decide_returns_none_when_no_create(self):
        from agent.decision import _call_llm
        with patch("agent.decision.os.environ.get", return_value=""):
            result = _call_llm(["test news"], 100.0)
            assert result.get("create_market") is False

    def test_decide_validates_output(self):
        from agent.decision import decide
        with patch("agent.decision._call_llm") as mock:
            mock.return_value = {
                "relevant": True,  # stage-1 filter verdict (same mock serves both stages)
                "create_market": True,
                "question": "Will ETH hit $5000?",
                "options": ["Yes", "No"],
                "hours": 24,
                "bet_outcome": 0,
                "bet_amount_usdc": 5.0,
                "confidence": 0.7,
                "reasoning": "Bullish trend",
            }
            result = decide(["<untrusted>test</untrusted>"], 100.0)
            assert result is not None
            assert result.question == "Will ETH hit $5000?"
            assert result.options == ["Yes", "No"]
            assert result.bet_amount_usdc == 5.0

    def test_decide_enforces_caps(self):
        from agent.decision import decide
        with patch("agent.decision._call_llm") as mock:
            mock.return_value = {
                "relevant": True,
                "create_market": True,
                "question": "Test?",
                "options": ["A", "B"],
                "hours": 24,
                "bet_outcome": 0,
                "bet_amount_usdc": 100.0,  # > $10 cap
                "confidence": 0.9,
                "reasoning": "test",
            }
            result = decide(["test"], 100.0)
            assert result is not None
            assert result.bet_amount_usdc == 10.0  # capped to PER_TX_CAP_USDC


# ---------------------------------------------------------------------------
# eas.py
# ---------------------------------------------------------------------------

class TestEAS:
    def test_attestation_data_encode(self):
        from agent.eas import AttestationData
        data = AttestationData(
            action_type="place_bet",
            market_id=42,
            amount_micro=5_000_000,
            confidence=75,
            reasoning="Bullish momentum",
        )
        encoded = data.encode_data()
        assert isinstance(encoded, bytes)
        assert len(encoded) > 0

    def test_reasoning_hash(self):
        from agent.eas import AttestationData
        data = AttestationData(
            action_type="create_market",
            market_id=1,
            amount_micro=10_000_000,
            confidence=80,
            reasoning="Test reasoning",
        )
        h = data.reasoning_hash
        assert isinstance(h, bytes)
        assert len(h) == 32

    def test_attest_action_local_fallback(self, monkeypatch, tmp_path):
        from agent import config as agent_config
        from agent.eas import AttestationData, attest_action
        monkeypatch.setattr(agent_config, "STATE_DIR", str(tmp_path))
        data = AttestationData(
            action_type="create_market",
            market_id=99,
            amount_micro=10_000_000,
            confidence=50,
            reasoning="Fallback test",
        )
        # Without AGENT_EAS_KEY, should log locally
        result = attest_action(data)
        assert result is None  # local fallback
        # Check local log was created — in STATE_DIR, not the CWD.
        log_file = agent_config.attestations_file()
        assert log_file.parent == tmp_path
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) > 0
        last = json.loads(lines[-1])
        assert last["market_id"] == 99

    def test_local_attestation_survives_unwritable_dir(self, monkeypatch, tmp_path, caplog):
        """_log_local is the fallback for an attestation that already failed: a
        full or read-only filesystem must be a warning, never a second crash."""
        import agent.eas as eas
        from agent.eas import AttestationData
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("x")
        monkeypatch.setattr(eas.config, "STATE_DIR", str(blocker / "missing"))
        with caplog.at_level(logging.WARNING, logger="agent.eas"):
            eas._log_local(AttestationData(
                action_type="place_bet", market_id=7, amount_micro=1,
                confidence=10, reasoning="r",
            ))
        assert "write failed" in caplog.text


# ---------------------------------------------------------------------------
# State paths — one writable place for every agent runtime file
# ---------------------------------------------------------------------------

class TestStatePaths:
    def test_blank_state_dir_counts_as_unset(self, monkeypatch):
        """prod.env.example ships AGENT_STATE_DIR= blank. '' would make every
        state path CWD-relative, and the audit trail's makedirs(dirname('')) is
        a no-write the writer swallows as 'read-only filesystem'."""
        from agent.config import resolve_state_dir

        monkeypatch.setenv("AGENT_STATE_DIR", "")
        assert resolve_state_dir().endswith("agent")
        monkeypatch.setenv("AGENT_STATE_DIR", "   ")
        assert resolve_state_dir().endswith("agent")

    def test_explicit_state_dir_wins(self, monkeypatch, tmp_path):
        from agent.config import resolve_state_dir

        monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path))
        assert resolve_state_dir() == str(tmp_path)

    def test_module_reads_env_at_import(self, monkeypatch, tmp_path):
        """STATE_DIR is a module constant, so the env contract has to hold for a
        freshly imported config too — that is what a container sees at boot."""
        import importlib

        import agent.config as agent_config

        monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path))
        try:
            importlib.reload(agent_config)
            assert str(tmp_path) == agent_config.STATE_DIR
            assert agent_config.audit_file() == tmp_path / agent_config.AUDIT_FILENAME
            assert agent_config.attestations_file() == tmp_path / agent_config.ATTESTATIONS_FILENAME
        finally:
            monkeypatch.undo()
            importlib.reload(agent_config)

    def test_paths_repoint_with_state_dir(self, monkeypatch, tmp_path):
        """Every state path is resolved per call, so a late STATE_DIR change
        (tests, or an operator setting AGENT_STATE_DIR after import) is honoured."""
        from agent import config as agent_config
        from agent import news as agent_news
        from agent import tools as agent_tools

        monkeypatch.setattr(agent_config, "STATE_DIR", str(tmp_path))
        assert agent_config.audit_file().parent == tmp_path
        assert agent_news.config.state_file(agent_news.SEEN_FILENAME).parent == tmp_path
        assert agent_tools._markets_file().parent == tmp_path

    def test_audit_writer_and_reader_agree(self, monkeypatch, tmp_path):
        """agent.main writes the trail; bot/handlers/agent tails it. Different
        packages, same path — a mismatch showed the owner 'no recent actions'."""
        from types import SimpleNamespace

        import agent.config as agent_config
        from agent.main import _log_audit
        from bot.handlers.agent import _audit_tail

        monkeypatch.setattr(agent_config, "STATE_DIR", str(tmp_path))
        _log_audit(7, SimpleNamespace(
            question="Will BTC hold $100k?", options=["Yes", "No"],
            bet_outcome=0, bet_amount_usdc=5.0, confidence=0.7, reasoning="momentum",
        ), True)

        entries = _audit_tail()
        assert [e["market_id"] for e in entries] == [7]
        assert entries[0]["bet_placed"] is True
        assert (tmp_path / "agent_audit.jsonl").exists()

    def test_attestation_writer_and_reader_agree(self, monkeypatch, tmp_path):
        from agent import config as agent_config
        from agent.eas import AttestationData, _log_local

        monkeypatch.setattr(agent_config, "STATE_DIR", str(tmp_path))
        _log_local(AttestationData(
            action_type="create_market", market_id=3, amount_micro=1_000_000,
            confidence=60, reasoning="r",
        ))
        lines = agent_config.attestations_file().read_text().splitlines()
        assert json.loads(lines[-1])["market_id"] == 3
