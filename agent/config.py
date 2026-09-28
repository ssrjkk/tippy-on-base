"""Agent configuration — spend caps, circuit breakers, API endpoints.

All limits are enforced in code, never in the LLM prompt.
"""

import os
from pathlib import Path


# Writable state directory (caps counters, seen-news, audit trail).
def resolve_state_dir() -> str:
    """AGENT_STATE_DIR, else the agent package dir. A blank AGENT_STATE_DIR — how
    prod.env.example ships it — must count as unset: '' would make every state
    path CWD-relative and the audit trail's makedirs(dirname) a silent no-write.
    """
    return (os.environ.get("AGENT_STATE_DIR") or "").strip() or str(Path(__file__).resolve().parent)


STATE_DIR = resolve_state_dir()

AUDIT_FILENAME = "agent_audit.jsonl"
ATTESTATIONS_FILENAME = "agent_attestations.jsonl"


def state_file(name: str) -> Path:
    """Path of a state file, resolved per call so a repointed STATE_DIR (tests, a
    late AGENT_STATE_DIR) is honoured instead of a path frozen at import."""
    return Path(STATE_DIR) / name


def audit_file() -> Path:
    """The agent's action trail. Writers (agent.main) and readers (/agent, the web
    API) all go through here, so they can never read a file nobody writes."""
    return state_file(AUDIT_FILENAME)


def attestations_file() -> Path:
    """The local attestation trail (agent.eas fallback when there is no on-chain
    schema UID). Same directory as the audit trail: both are the operator's
    record of what the agent did, and both must survive a CWD change."""
    return state_file(ATTESTATIONS_FILENAME)


# --- Spend caps (USDC, not micro) ---------------------------------------------------
DAILY_SPEND_CAP_USDC = float(os.environ.get("AGENT_DAILY_CAP", "50"))
PER_TX_CAP_USDC = float(os.environ.get("AGENT_TX_CAP", "10"))
MAX_ACTIONS_PER_HOUR = int(os.environ.get("AGENT_ACTIONS_PER_HOUR", "20"))
MAX_BET_OWN_MARKETS_PCT = 0.10  # 10% of pool max on own markets

# --- Risk management ----------------------------------------------------------------
AGENT_STOP_LOSS_PCT = float(os.environ.get("AGENT_STOP_LOSS_PCT", "20"))  # 20% drawdown = halt

# --- Circuit breaker ----------------------------------------------------------------
MAX_CONSECUTIVE_ERRORS = int(os.environ.get("AGENT_MAX_ERRORS", "3"))
COOLDOWN_SECONDS = int(os.environ.get("AGENT_COOLDOWN_SECS", "300"))

# --- API endpoints ------------------------------------------------------------------
TIPPY_BASE_URL = os.environ.get("TIPPY_BASE_URL", "http://localhost:8000")
AGENT_TG_ID = int(os.environ.get("AGENT_TG_ID", "0"))  # 0 = unauthenticated demo

# --- News sources -------------------------------------------------------------------
NEWS_CHECK_INTERVAL = int(os.environ.get("AGENT_NEWS_INTERVAL", "300"))  # seconds

# --- LLM ---------------------------------------------------------------------------
LLM_MODEL = os.environ.get("AGENT_LLM_MODEL", "gpt-4o-mini")
LLM_BUDGET_DAILY = float(os.environ.get("AGENT_LLM_BUDGET", "5.00"))  # USD/day

# --- Chain --------------------------------------------------------------------------
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_RPC_URL = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")

# --- Money math ---------------------------------------------------------------------
MICRO = 10**6  # 1 USDC = 1e6 micro


def validate() -> list[str]:
    """Return a list of config errors. Call at startup and refuse to run if
    any are present — a misconfigured cap (zero or per-tx > daily) could
    otherwise drain the hot wallet or parse into an expensive default."""
    errors: list[str] = []
    if not (
        DAILY_SPEND_CAP_USDC > 0 and PER_TX_CAP_USDC > 0
    ):
        errors.append("AGENT_DAILY_CAP and AGENT_TX_CAP must both be > 0 USDC")
    if PER_TX_CAP_USDC > DAILY_SPEND_CAP_USDC:
        errors.append(
            f"AGENT_TX_CAP ({PER_TX_CAP_USDC}) must be <= AGENT_DAILY_CAP ({DAILY_SPEND_CAP_USDC})"
        )
    if MAX_ACTIONS_PER_HOUR <= 0:
        errors.append("AGENT_ACTIONS_PER_HOUR must be > 0")
    if MAX_CONSECUTIVE_ERRORS <= 0 or COOLDOWN_SECONDS <= 0:
        errors.append("AGENT_MAX_ERRORS and AGENT_COOLDOWN_SECS must be > 0")
    if not (0 < AGENT_STOP_LOSS_PCT <= 100):
        errors.append("AGENT_STOP_LOSS_PCT must be between 0 and 100")
    return errors
