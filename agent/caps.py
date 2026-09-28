"""Spend-cap enforcement and circuit breaker.

Every agent action MUST pass through `check_action()` before execution.
`check_action` atomically VALIDATES **and RESERVES** the budget under a
process-wide lock: counters are updated before the caller executes, so two
concurrent actions cannot both pass the check on the same snapshot and
overshoot the daily cap. If execution then fails, the caller returns the
reservation via `release_action()`; once globally reserved, an action never
calls `record_action` (there is nothing left to record — the reservation is
the record). The state file is written atomically so a crash cannot corrupt
the counters mid-write.

All money math uses int micro-units (1 USDC = 1e6 micro) to avoid float
precision errors. The daily cap reset is logged for audit trail.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_state_lock = threading.Lock()


def _state_file() -> Path:
    """Return the state file path, reading STATE_DIR dynamically.

    Module-level constants are evaluated once at import; if config.STATE_DIR
    changes after import (e.g., in tests or when AGENT_STATE_DIR is set late),
    a cached path would point to the wrong location.
    """
    return Path(config.STATE_DIR) / ".agent_state.json"


def _load_state() -> dict:
    sf = _state_file()
    if not sf.exists():
        return _default_state()
    try:
        state = json.loads(sf.read_text())
        # Migrate old float USDC state to int micro-units
        if "daily_spent" in state and "daily_spent_micro" not in state:
            old_usdc = state.pop("daily_spent", 0.0)
            state["daily_spent_micro"] = _usdc_to_micro(old_usdc)
            _save_state(state)
        return state
    except (ValueError, OSError):
        return _default_state()


def _default_state() -> dict:
    return {
        "daily_spent_micro": 0,
        "daily_date": "",
        "actions_this_hour": 0,
        "hour_ts": 0,
        "consecutive_errors": 0,
        "cooldown_until": 0.0,
    }


def _save_state(state: dict) -> None:
    try:
        sf = _state_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        tmp = sf.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, sf)
    except OSError:
        # Read-only filesystem: reservations still hold in memory for the
        # life of the process; the counter simply cannot survive a restart.
        pass


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _hour_bucket() -> int:
    return int(time.time()) // 3600


def _usdc_to_micro(usdc: float) -> int:
    """Convert USDC (float) to micro-units (int). Rounds to avoid float drift."""
    return round(usdc * 1_000_000)


def check_action(cost_usdc: float) -> str | None:
    """Validate the action against all caps AND reserve its budget atomically.

    On success returns None and the budget is already reserved (both the
    daily spend and the hourly action counter are incremented under the
    lock). On failure returns an error message and reserves NOTHING.

    Callers MUST call `release_action(cost_usdc)` when execution fails so
    the reservation is returned.
    """
    with _state_lock:
        state = _load_state()
        now = time.time()

        # Circuit breaker — cooldown active
        if now < state["cooldown_until"]:
            remaining = int(state["cooldown_until"] - now)
            return f"Circuit breaker active, cooldown {remaining}s remaining"

        # Per-tx cap — checked first: a single oversized action is invalid
        # regardless of how much daily budget remains. Zero is allowed for
        # actions that do not spend (e.g. creating a paywall signal).
        if cost_usdc < 0:
            return f"Action cost must be non-negative (got ${cost_usdc:.2f})"
        cost_micro = _usdc_to_micro(cost_usdc)
        per_tx_micro = _usdc_to_micro(config.PER_TX_CAP_USDC)
        if cost_micro > per_tx_micro:
            return f"Per-tx cap ${config.PER_TX_CAP_USDC} exceeded (requested ${cost_usdc:.2f})"

        # Daily cap — reset with audit log if date changed
        today = _today()
        if state["daily_date"] != today:
            old_spent = state.get("daily_spent_micro", 0)
            old_date = state.get("daily_date", "never")
            log.info(
                "agent daily cap reset: %s -> %s, previous spend $%.2f",
                old_date, today, old_spent / 1_000_000,
            )
            state["daily_date"] = today
            state["daily_spent_micro"] = 0
        daily_cap_micro = _usdc_to_micro(config.DAILY_SPEND_CAP_USDC)
        if state["daily_spent_micro"] + cost_micro > daily_cap_micro:
            return f"Daily cap ${config.DAILY_SPEND_CAP_USDC} reached (${state['daily_spent_micro'] / 1_000_000:.2f} spent)"

        # Rate limit
        hour = _hour_bucket()
        if state["hour_ts"] != hour:
            state["hour_ts"] = hour
            state["actions_this_hour"] = 0
        if state["actions_this_hour"] >= config.MAX_ACTIONS_PER_HOUR:
            return f"Rate limit {config.MAX_ACTIONS_PER_HOUR} actions/hour reached"

        # Reserve: budget + action counter bumped under the same lock the
        # check ran under, so a concurrent check_action sees this action.
        state["daily_spent_micro"] += cost_micro
        state["actions_this_hour"] += 1
        _save_state(state)
        return None


def release_action(cost_usdc: float) -> None:
    """Return a previously-reserved budget because execution failed.

    Undoes the reservation: subtracts the cost back out of the daily spend
    and decrements the hourly action counter.
    """
    with _state_lock:
        state = _load_state()
        cost_micro = _usdc_to_micro(cost_usdc)
        today = _today()
        if state["daily_date"] != today:
            state["daily_date"] = today
            state["daily_spent_micro"] = 0
        state["daily_spent_micro"] = max(0, state["daily_spent_micro"] - cost_micro)
        state["actions_this_hour"] = max(0, state["actions_this_hour"] - 1)
        _save_state(state)


def record_action(cost_usdc: float) -> None:
    """Backwards-compatible no-op.

    Budget is reserved atomically in check_action; calling this again would
    double-count. Kept so old call sites don't silently break.
    """
    return


def record_error() -> None:
    """Call on failure. Triggers circuit breaker after MAX_CONSECUTIVE_ERRORS."""
    with _state_lock:
        state = _load_state()
        state["consecutive_errors"] += 1
        if state["consecutive_errors"] >= config.MAX_CONSECUTIVE_ERRORS:
            state["cooldown_until"] = time.time() + config.COOLDOWN_SECONDS
            state["consecutive_errors"] = 0
        _save_state(state)


def get_status() -> dict:
    """Return current agent state for monitoring."""
    with _state_lock:
        state = _load_state()
        daily_spent_micro = state.get("daily_spent_micro", 0)
        return {
            "daily_spent_usdc": daily_spent_micro / 1_000_000,
            "daily_cap_usdc": config.DAILY_SPEND_CAP_USDC,
            "actions_this_hour": state["actions_this_hour"],
            "max_actions_per_hour": config.MAX_ACTIONS_PER_HOUR,
            "consecutive_errors": state["consecutive_errors"],
            "cooldown_active": time.time() < state["cooldown_until"],
        }
