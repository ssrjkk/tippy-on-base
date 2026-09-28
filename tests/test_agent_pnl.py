"""Agent PnL attribution + drawdown stop-loss (agent/pnl.py).

Only the ledger is mocked here — it is the I/O boundary. The caps counters,
the state file and the halt latch all run for real against a tmp STATE_DIR.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from agent import caps, config, pnl

AGENT = 999
M = 1_000_000


class FakeLedger:
    """Stands in for AsyncLedger: cash, open positions, resolved markets."""

    def __init__(self, balance_usdc=0.0, positions=None, views=None):
        self.balance_usdc = balance_usdc
        self.positions = positions or []
        self.views = views or {}
        self.views_calls = 0

    async def balance(self, tg_id):
        return Decimal(str(self.balance_usdc))

    async def user_market_positions(self, tg_id):
        return [dict(p) for p in self.positions]

    async def bulk_amm_market_views(self, market_ids):
        self.views_calls += 1
        wanted = set(market_ids)
        return {k: v for k, v in self.views.items() if k in wanted}


def _pos(cost_micro, value_micro, market_id=1):
    return {"market_id": market_id, "cost": cost_micro, "value": value_micro}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point every runtime-state file at a tmp dir (caps resolves STATE_DIR too)."""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "AGENT_STOP_LOSS_PCT", 20.0)
    return tmp_path


@pytest.fixture
def state_file(isolated_state):
    return isolated_state / ".agent_pnl.json"


def _pnl_state(state_file):
    return json.loads(state_file.read_text())


# ---------------------------------------------------------------------------
# state file
# ---------------------------------------------------------------------------

class TestStateFile:
    @pytest.mark.asyncio
    async def test_record_trade_appends(self, state_file):
        await pnl.record_trade(7, 1, 3 * M, 2 * M)
        (trade,) = _pnl_state(state_file)["trades"]
        assert trade == {
            "market_id": 7, "outcome": 1, "shares": 3 * M,
            "cost_micro": 2 * M, "timestamp": trade["timestamp"],
            "resolved": False,
        }

    @pytest.mark.asyncio
    async def test_missing_file_defaults(self, isolated_state, monkeypatch):
        fake = FakeLedger(10.0)
        monkeypatch.setattr(pnl, "ledger", fake)
        report = await pnl.get_pnl_report(AGENT)
        assert report["peak_equity_micro"] == 10 * M
        assert report["total_trades"] == 0
        assert fake.views_calls == 0  # nothing recorded, so no status query

    @pytest.mark.asyncio
    async def test_corrupt_file_defaults(self, state_file, monkeypatch):
        state_file.write_text("{not json")
        monkeypatch.setattr(pnl, "ledger", FakeLedger(10.0))
        assert (await pnl.get_pnl_report(AGENT))["total_trades"] == 0

    @pytest.mark.asyncio
    async def test_legacy_peak_balance_key_migrates(self, state_file, monkeypatch):
        state_file.write_text(json.dumps({"trades": [], "peak_balance_micro": 40 * M}))
        monkeypatch.setattr(pnl, "ledger", FakeLedger(10.0))
        report = await pnl.get_pnl_report(AGENT)
        assert report["peak_equity_micro"] == 40 * M
        assert "peak_balance_micro" not in _pnl_state(state_file)

    def test_save_failure_is_logged_not_raised(self, isolated_state, monkeypatch, caplog):
        monkeypatch.setattr(
            pnl.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only"))
        )
        with caplog.at_level("WARNING"):
            pnl._save_pnl_state(pnl._load_pnl_state())
        assert "Failed to save PnL state" in caplog.text
        assert not (isolated_state / ".agent_pnl.json").exists()


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------

class TestAttribution:
    @pytest.mark.asyncio
    async def test_winning_trade_pays_one_micro_per_share(self, monkeypatch):
        await pnl.record_trade(1, 0, 3 * M, 2 * M)
        monkeypatch.setattr(pnl, "ledger", FakeLedger(
            10.0, views={1: {"status": "resolved", "winner": 0}},
        ))
        report = await pnl.get_pnl_report(AGENT)
        assert report["realized_pnl_micro"] == 1 * M
        assert report["resolved_trades"] == 1

    @pytest.mark.asyncio
    async def test_losing_trade_books_full_cost(self, monkeypatch):
        await pnl.record_trade(1, 0, 3 * M, 2 * M)
        monkeypatch.setattr(pnl, "ledger", FakeLedger(
            10.0, views={1: {"status": "resolved", "winner": 1}},
        ))
        assert (await pnl.get_pnl_report(AGENT))["realized_pnl_micro"] == -2 * M

    @pytest.mark.asyncio
    async def test_resolved_market_with_no_winner_booked_as_loss(self, monkeypatch):
        await pnl.record_trade(1, 0, 3 * M, 2 * M)
        monkeypatch.setattr(pnl, "ledger", FakeLedger(
            10.0, views={1: {"status": "resolved", "winner": None}},
        ))
        assert (await pnl.get_pnl_report(AGENT))["realized_pnl_micro"] == -2 * M

    @pytest.mark.asyncio
    async def test_resolved_trades_are_not_double_counted(self, monkeypatch):
        await pnl.record_trade(1, 0, 3 * M, 2 * M)
        fake = FakeLedger(10.0, views={1: {"status": "resolved", "winner": 0}})
        monkeypatch.setattr(pnl, "ledger", fake)
        first = await pnl.get_pnl_report(AGENT)
        second = await pnl.get_pnl_report(AGENT)
        assert first["realized_pnl_micro"] == second["realized_pnl_micro"] == 1 * M
        assert fake.views_calls == 1  # second call has nothing pending

    @pytest.mark.asyncio
    async def test_open_market_books_no_realized_pnl(self, monkeypatch):
        await pnl.record_trade(1, 0, 5 * M, 5 * M)
        fake = FakeLedger(10.0, positions=[_pos(5 * M, 4 * M)])
        monkeypatch.setattr(pnl, "ledger", fake)
        report = await pnl.get_pnl_report(AGENT)
        assert report["realized_pnl_micro"] == 0
        assert report["resolved_trades"] == 0
        assert report["unrealized_pnl_micro"] == -1 * M
        assert report["total_pnl_micro"] == -1 * M
        assert fake.views_calls == 1  # the open market's status was checked

    @pytest.mark.asyncio
    async def test_unrealized_is_marked_to_market(self, monkeypatch):
        monkeypatch.setattr(pnl, "ledger", FakeLedger(10.0, positions=[
            _pos(5 * M, 4 * M), _pos(2 * M, 3 * M, market_id=2),
        ]))
        report = await pnl.get_pnl_report(AGENT)
        assert report["unrealized_pnl_micro"] == 0  # -1M on #1, +1M on #2
        assert report["equity_micro"] == 17 * M     # 10M cash + 7M cost basis
        assert report["total_pnl_micro"] == 0
        assert report["drawdown_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_peak_equity_only_rises(self, monkeypatch):
        monkeypatch.setattr(pnl, "ledger", FakeLedger(30.0))
        assert (await pnl.get_pnl_report(AGENT))["peak_equity_micro"] == 30 * M
        monkeypatch.setattr(pnl, "ledger", FakeLedger(12.0))
        report = await pnl.get_pnl_report(AGENT)
        assert report["peak_equity_micro"] == 30 * M
        assert round(report["drawdown_pct"], 1) == 60.0


# ---------------------------------------------------------------------------
# stop-loss latch
# ---------------------------------------------------------------------------

async def _establish_peak(monkeypatch, cash, cost, value):
    """First cycle: sets the peak at cash + cost basis, and must not halt."""
    monkeypatch.setattr(pnl, "ledger", FakeLedger(cash, positions=[_pos(cost, value)]))
    assert await pnl.check_drawdown(AGENT) is None


class TestStopLoss:
    @pytest.mark.asyncio
    async def test_zero_equity_never_halts(self, monkeypatch):
        monkeypatch.setattr(pnl, "ledger", FakeLedger(0.0))
        assert await pnl.check_drawdown(AGENT) is None

    @pytest.mark.asyncio
    async def test_subsidy_outlay_does_not_trip(self, monkeypatch):
        """Funding a market moves cash into escrow with no position to show.
        That is an outlay, not a loss — a balance-based drawdown would halt the
        agent on its very first market."""
        await _establish_peak(monkeypatch, cash=10.0, cost=5 * M, value=5 * M)
        monkeypatch.setattr(pnl, "ledger", FakeLedger(5.0))  # $5 gone to escrow
        assert await pnl.check_drawdown(AGENT) is None

    @pytest.mark.asyncio
    async def test_trips_on_unrealized_loss(self, monkeypatch):
        await _establish_peak(monkeypatch, cash=10.0, cost=5 * M, value=5 * M)
        monkeypatch.setattr(pnl, "ledger", FakeLedger(
            10.0, positions=[_pos(5 * M, 500_000)],
        ))
        err = await pnl.check_drawdown(AGENT)
        assert err is not None and "Stop-loss active" in err
        assert "-$4.50" in err and "$3.00" in err

    @pytest.mark.asyncio
    async def test_trips_on_realized_loss(self, monkeypatch):
        await _establish_peak(monkeypatch, cash=10.0, cost=5 * M, value=5 * M)
        await pnl.record_trade(1, 0, 5 * M, 5 * M)
        monkeypatch.setattr(pnl, "ledger", FakeLedger(
            10.0, views={1: {"status": "resolved", "winner": 1}},
        ))
        assert "Stop-loss active" in await pnl.check_drawdown(AGENT)

    @pytest.mark.asyncio
    async def test_halts_until_half_the_threshold(self, monkeypatch, state_file):
        await _establish_peak(monkeypatch, cash=10.0, cost=5 * M, value=5 * M)
        fake = FakeLedger(10.0, positions=[_pos(5 * M, 1_600_000)])  # 3.4M loss
        monkeypatch.setattr(pnl, "ledger", fake)
        assert "Stop-loss active" in await pnl.check_drawdown(AGENT)
        assert _pnl_state(state_file)["halted"] is True

        # 2.0M loss: under the 3.0M trip line but above the 1.5M release line,
        # so the latch stays on instead of flapping with every price tick.
        fake.positions = [_pos(5 * M, 3 * M)]
        assert "Stop-loss active" in await pnl.check_drawdown(AGENT)

        # 1.4M loss: released.
        fake.positions = [_pos(5 * M, 3_600_000)]
        assert await pnl.check_drawdown(AGENT) is None
        assert _pnl_state(state_file)["halted"] is False

    @pytest.mark.asyncio
    async def test_threshold_is_configurable(self, monkeypatch):
        monkeypatch.setattr(config, "AGENT_STOP_LOSS_PCT", 5.0)  # 15M * 5% = 0.75M
        await _establish_peak(monkeypatch, cash=10.0, cost=5 * M, value=5 * M)
        monkeypatch.setattr(pnl, "ledger", FakeLedger(
            10.0, positions=[_pos(5 * M, 4_500_000)],
        ))
        assert await pnl.check_drawdown(AGENT) is None  # 0.5M loss, under 0.75M
        monkeypatch.setattr(pnl, "ledger", FakeLedger(
            10.0, positions=[_pos(5 * M, 4_000_000)],
        ))
        assert "Stop-loss active" in await pnl.check_drawdown(AGENT)  # 1.0M loss


# ---------------------------------------------------------------------------
# the gate the tools actually call
# ---------------------------------------------------------------------------

class TestGate:
    @pytest.mark.asyncio
    async def test_allows_and_reserves(self, monkeypatch):
        monkeypatch.setattr(pnl, "ledger", FakeLedger(10.0))
        assert await pnl.check_action_with_drawdown(AGENT, 5.0) is None
        assert caps._load_state()["daily_spent_micro"] == 5 * M

    @pytest.mark.asyncio
    async def test_halting_leaves_the_cap_budget_untouched(self, monkeypatch, state_file):
        await _establish_peak(monkeypatch, cash=10.0, cost=5 * M, value=5 * M)
        monkeypatch.setattr(pnl, "ledger", FakeLedger(
            10.0, positions=[_pos(9 * M, 0)],  # total loss
        ))
        err = await pnl.check_action_with_drawdown(AGENT, 5.0)
        assert "Stop-loss active" in err
        assert caps._load_state()["daily_spent_micro"] == 0
        assert _pnl_state(state_file)["halted"] is True

    @pytest.mark.asyncio
    async def test_caps_still_apply_after_the_gate(self, monkeypatch):
        monkeypatch.setattr(pnl, "ledger", FakeLedger(10.0))
        assert "Per-tx cap" in await pnl.check_action_with_drawdown(AGENT, 99.0)
        assert caps._load_state()["daily_spent_micro"] == 0


# ---------------------------------------------------------------------------
# wiring: agent/tools.py must actually go through the gate
# ---------------------------------------------------------------------------

class _StubLedger:
    def __init__(self, ok=True):
        self.ok = ok
        self.buys = []

    async def buy_shares(self, market_id, tg_id, option_idx, micro):
        self.buys.append((market_id, tg_id, option_idx, micro))
        if not self.ok:
            return "closed", {}
        return "ok", {"shares": 2 * M, "cost": micro, "price": 0.4, "label": "Yes"}

    async def balance(self, tg_id):
        return Decimal("10")

    async def resolve_market(self, market_id, winning_idx, resolver_id):
        return True, "ok", []


@pytest.fixture
def wired_tools(monkeypatch, isolated_state):
    """Real tools.py + real pnl.py + real caps.py, stubbed database."""
    from agent import tools

    stub = _StubLedger()
    monkeypatch.setattr(tools, "ledger", stub)
    monkeypatch.setattr(pnl, "ledger", FakeLedger(10.0))
    monkeypatch.setattr(config, "AGENT_TG_ID", AGENT)
    tools._agent_markets.clear()
    for f in Path(isolated_state).glob(".agent_*.json"):
        f.unlink()
    return tools, stub


class TestWiring:
    @pytest.mark.asyncio
    async def test_place_bet_records_the_fill(self, wired_tools, state_file):
        tools, _stub = wired_tools
        assert (await tools.place_bet(5, 1, 2.0))["status"] == "ok"
        (trade,) = _pnl_state(state_file)["trades"]
        assert trade["market_id"] == 5 and trade["outcome"] == 1
        assert trade["shares"] == 2 * M and trade["cost_micro"] == 2 * M

    @pytest.mark.asyncio
    async def test_failed_bet_records_nothing(self, wired_tools, state_file):
        tools, stub = wired_tools
        stub.ok = False
        assert "error" in await tools.place_bet(5, 1, 2.0)
        assert _pnl_state(state_file)["trades"] == []
        assert caps._load_state()["daily_spent_micro"] == 0  # reservation returned

    @pytest.mark.asyncio
    async def test_stop_loss_blocks_the_bet_before_the_ledger(self, wired_tools, monkeypatch):
        tools, stub = wired_tools

        async def halted(_tg_id):
            return "Stop-loss active: forced in test"

        monkeypatch.setattr(pnl, "check_drawdown", halted)
        result = await tools.place_bet(5, 1, 2.0)
        assert result == {"error": "Stop-loss active: forced in test"}
        assert stub.buys == []
        assert caps._load_state()["daily_spent_micro"] == 0

    @pytest.mark.asyncio
    async def test_create_market_goes_through_the_gate(self, wired_tools, monkeypatch):
        tools, _stub = wired_tools
        seen = {}

        async def fake_gate(tg_id, cost_usdc):
            seen["args"] = (tg_id, cost_usdc)
            return "Stop-loss active: blocked"

        monkeypatch.setattr(pnl, "check_action_with_drawdown", fake_gate)
        assert await tools.create_market("Q?", ["A", "B"], subsidy_usdc=10.0) == {
            "error": "Stop-loss active: blocked"
        }
        assert seen["args"] == (AGENT, 10.0)

    @pytest.mark.asyncio
    async def test_resolve_market_unpacks_the_ledger_tuple(self, wired_tools):
        """resolve_market returns (ok, message, payouts); unpacking two values
        raised ValueError, which the tool reported as a resolve failure."""
        tools, _stub = wired_tools
        assert await tools.resolve_market(3, 0) == {"status": "ok"}
