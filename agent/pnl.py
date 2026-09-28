"""Agent PnL attribution and stop-loss on drawdown.

`get_pnl_report` attributes the agent's performance to realized PnL (trades
in resolved markets) plus unrealized PnL (mark-to-market of open positions).
`check_action_with_drawdown` refuses to spend while that combined PnL sits
`AGENT_STOP_LOSS_PCT` below the agent's high-water equity.

Why PnL and not the ledger balance: every bet and every market subsidy moves
cash into market escrow, so a balance-based drawdown would report an
ordinary, perfectly healthy open position as a loss and halt the agent after
its first trade. PnL only goes negative when the agent is actually wrong.

The halt latches with hysteresis: once tripped it stays on until PnL recovers
to half the threshold, otherwise the agent would resume on the first
favourable tick and halt again on the next. Fast protection against a burst
of errors is caps.py's circuit breaker and its per-day / per-hour limits;
this is the slower structural brake.

All money math is int micro-units (1 USDC = 1e6 micro).
"""

import json
import logging
import os
import time
from pathlib import Path

from bot.ledger import async_ledger as ledger

from . import caps, config

log = logging.getLogger(__name__)

_PNL_FILE = ".agent_pnl.json"


def _pnl_file() -> Path:
    """Resolve the state path on every call: config.STATE_DIR can change after
    import (tests, a late AGENT_STATE_DIR), the same reason caps.py does this."""
    return Path(config.STATE_DIR) / _PNL_FILE


def _default_state() -> dict:
    return {"trades": [], "peak_equity_micro": 0, "halted": False}


def _load_pnl_state() -> dict:
    pf = _pnl_file()
    if not pf.exists():
        return _default_state()
    try:
        state = json.loads(pf.read_text())
    except (ValueError, OSError):
        return _default_state()
    # Rename from the earlier balance-based peak; a state file written before
    # it existed carries neither key and defaults to 0 below.
    if "peak_equity_micro" not in state and "peak_balance_micro" in state:
        state["peak_equity_micro"] = state.pop("peak_balance_micro")
    state.setdefault("trades", [])
    state.setdefault("peak_equity_micro", 0)
    state.setdefault("halted", False)
    return state


def _save_pnl_state(state: dict) -> None:
    pf = _pnl_file()
    try:
        pf.parent.mkdir(parents=True, exist_ok=True)
        tmp = pf.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, pf)
    except OSError as e:
        log.warning("Failed to save PnL state: %s", e)


async def record_trade(market_id: int, outcome: int, shares: int, cost_micro: int) -> None:
    """Record a filled AMM buy so :func:`get_pnl_report` can attribute it later."""
    state = _load_pnl_state()
    state["trades"].append({
        "market_id": int(market_id),
        "outcome": int(outcome),
        "shares": int(shares),
        "cost_micro": int(cost_micro),
        "timestamp": int(time.time()),
        "resolved": False,
    })
    _save_pnl_state(state)


async def _realized_pnl_micro(trades: list[dict]) -> int:
    """Net PnL over recorded trades whose market has resolved.

    Marks those trades resolved in the caller's state so the figure does not
    double-count on the next call. Trades still sitting in an open or
    cancelled market book nothing: their cash is either valued by
    `unrealized_pnl_micro` or has already been refunded by the ledger.
    """
    pending = sorted({int(t["market_id"]) for t in trades if not t.get("resolved")})
    views = await ledger.bulk_amm_market_views(pending) if pending else {}
    total = 0
    for trade in trades:
        if not trade.get("resolved"):
            market = views.get(int(trade["market_id"]))
            if not market or market["status"] != "resolved":
                continue
            # A winning micro-share redeems for exactly 1 micro-USDC
            # (LedgerMarkets.resolve_market pays every winner share 1 USDC).
            winner = market["winner"]
            trade["won"] = winner is not None and int(winner) == trade["outcome"]
            trade["resolved"] = True
        total += (trade["shares"] if trade.get("won") else 0) - trade["cost_micro"]
    return total


async def get_pnl_report(tg_id: int) -> dict:
    """Full PnL attribution plus the equity figures the stop-loss runs on."""
    state = _load_pnl_state()
    positions = await ledger.user_market_positions(tg_id)
    unrealized = sum(int(p["value"]) - int(p["cost"]) for p in positions)
    realized = await _realized_pnl_micro(state["trades"])
    balance = int(await ledger.balance(tg_id) * config.MICRO)
    open_cost = sum(int(p["cost"]) for p in positions)
    equity = balance + open_cost + unrealized
    peak = max(int(state["peak_equity_micro"]), equity)
    state["peak_equity_micro"] = peak
    _save_pnl_state(state)

    drawdown_pct = 0.0 if peak <= 0 else (peak - equity) / peak * 100
    return {
        "total_trades": len(state["trades"]),
        "resolved_trades": sum(1 for t in state["trades"] if t.get("resolved")),
        "realized_pnl_micro": realized,
        "unrealized_pnl_micro": unrealized,
        "total_pnl_micro": realized + unrealized,
        "peak_equity_micro": peak,
        "equity_micro": equity,
        "balance_micro": balance,
        "drawdown_pct": drawdown_pct,
        "halted": bool(state["halted"]),
    }


async def check_drawdown(tg_id: int) -> str | None:
    """Return a block message while the stop-loss is tripped, else None.

    Trips when realized + unrealized PnL falls below
    ``-AGENT_STOP_LOSS_PCT`` percent of peak equity; releases at half that.
    """
    report = await get_pnl_report(tg_id)
    peak = report["peak_equity_micro"]
    if peak <= 0:
        return None

    stop_pct = config.AGENT_STOP_LOSS_PCT
    threshold = peak * stop_pct / 100
    loss = -report["total_pnl_micro"]

    halted = report["halted"]
    if not halted and loss >= threshold:
        halted = True
        log.error(
            "agent stop-loss tripped: PnL %d micro <= -%.1f%% of peak equity %d micro",
            report["total_pnl_micro"], stop_pct, peak,
        )
    elif halted and loss <= threshold / 2:
        halted = False
        log.info("agent stop-loss released: PnL recovered to %d micro", report["total_pnl_micro"])

    if halted != report["halted"]:
        state = _load_pnl_state()
        state["halted"] = halted
        _save_pnl_state(state)

    if halted:
        return (
            f"Stop-loss active: PnL -${loss / config.MICRO:.2f} against a "
            f"${threshold / config.MICRO:.2f} limit ({stop_pct}% of peak equity)"
        )
    return None


async def check_action_with_drawdown(tg_id: int, cost_usdc: float) -> str | None:
    """Stop-loss gate followed by :func:`caps.check_action`.

    Returns an error message and reserves nothing when blocked, or None once
    the budget is reserved. Callers keep the existing ``release_action``
    contract for actions that then fail to execute.
    """
    drawdown_err = await check_drawdown(tg_id)
    if drawdown_err:
        return drawdown_err
    return caps.check_action(cost_usdc)
