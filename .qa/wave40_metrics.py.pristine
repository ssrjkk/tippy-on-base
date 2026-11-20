"""Prometheus text exposition served by the origin at `/metrics` on `WEB_PORT`.

The names below are the whole surface — `tipbot_liabilities_usdc`,
`tipbot_reserves_usdc`, `tipbot_solvent`, `tipbot_users_total`,
`tipbot_markets_open`, `tipbot_volume_usdc` — and
`tests/test_metrics.py` holds this list, the `# TYPE` lines and the bullets in
`docs/OPERATIONS.md` to one set, because a metric the runbook promises and the
formatter does not print is a dashboard panel that silently reads no data.

A leg that could not be measured answers `-1`: the gauge is never dropped, so a
scrape that lost the RPC still draws a line and an alert on `tipbot_solvent == 0`
still fires instead of going quiet. Values are refilled at most every
`_CACHE_TTL` seconds — without that, a 15 s scrape would drive four aggregate
queries and an on-chain balance read every interval.

The reserves line is the hot wallet, or the vault when `VAULT_ADDRESS` is set. It
does NOT count the two in-flight x402 legs that `bot/solvency.py` adds to its own
reserves (unswept booked credit, still parked on the per-invoice derived address,
and the receive pool the sweep has not drained), so a delayed sweep can make this
gauge read short while the canary that alerts the owner stays quiet. `/api/solvency`
carries the same narrower definition.
"""

import time
from decimal import Decimal

from bot import base, config
from bot.ledger import async_ledger as ledger

_MICRO = 10 ** config.USDC_DECIMALS
_metrics_cache: dict | None = None
_metrics_ts: float = 0.0
_CACHE_TTL = 30  # seconds


async def collect_metrics() -> str:
    """Collect and format metrics in Prometheus text format."""
    global _metrics_cache, _metrics_ts

    now = time.time()
    if _metrics_cache and now - _metrics_ts < _CACHE_TTL:
        return _format_metrics(_metrics_cache)

    m = {}

    # Solvency
    try:
        liabilities = await ledger.total_liabilities()
        m["liabilities_usdc"] = float(Decimal(liabilities) / Decimal(_MICRO))
    except Exception:
        m["liabilities_usdc"] = -1

    try:
        vault_addr = config.VAULT_ADDRESS
        if vault_addr:
            reserves = await base.vault_balance()
        else:
            reserves = await base.hot_balance()
        # base.hot_balance()/vault_balance() already return human USDC floats;
        # do NOT divide by _MICRO again (old code made solvent always false).
        m["reserves_usdc"] = reserves if reserves is not None else -1
    except Exception:
        m["reserves_usdc"] = -1

    m["solvent"] = 1 if (
        m["reserves_usdc"] >= 0 and m["liabilities_usdc"] >= 0
        and m["reserves_usdc"] >= m["liabilities_usdc"]
    ) else 0

    # Stats — only the figures the exposition prints. A value collected and never
    # formatted is work no scrape reads; `test_the_collector_writes_exactly_what_the_formatter_reads`
    # refuses it.
    try:
        s = await ledger.global_stats()
        m["users_total"] = s.get("users", 0)
        m["volume_usdc"] = float(Decimal(s.get("volume_micro", 0)) / Decimal(_MICRO))
    except Exception:
        m["users_total"] = 0
        m["volume_usdc"] = 0

    # Open markets
    try:
        markets = await ledger.open_markets(1000)
        m["markets_open"] = len(markets)
    except Exception:
        m["markets_open"] = 0

    _metrics_cache = m
    _metrics_ts = now
    return _format_metrics(m)


def _format_metrics(m: dict) -> str:
    lines = [
        "# HELP tipbot_liabilities_usdc Total user liabilities in USDC",
        "# TYPE tipbot_liabilities_usdc gauge",
        f"tipbot_liabilities_usdc {m.get('liabilities_usdc', -1)}",
        "",
        "# HELP tipbot_reserves_usdc On-chain reserves in USDC",
        "# TYPE tipbot_reserves_usdc gauge",
        f"tipbot_reserves_usdc {m.get('reserves_usdc', -1)}",
        "",
        "# HELP tipbot_solvent Whether the bot is solvent (1=yes, 0=no)",
        "# TYPE tipbot_solvent gauge",
        f"tipbot_solvent {m.get('solvent', 0)}",
        "",
        "# HELP tipbot_users_total Total registered users",
        "# TYPE tipbot_users_total gauge",
        f"tipbot_users_total {m.get('users_total', 0)}",
        "",
        "# HELP tipbot_markets_open Currently open prediction markets",
        "# TYPE tipbot_markets_open gauge",
        f"tipbot_markets_open {m.get('markets_open', 0)}",
        "",
        "# HELP tipbot_volume_usdc Total volume in USDC",
        "# TYPE tipbot_volume_usdc counter",
        f"tipbot_volume_usdc {m.get('volume_usdc', 0)}",
        "",
    ]
    return "\n".join(lines)
