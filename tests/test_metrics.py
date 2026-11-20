"""Metrics endpoint: numeric correctness (reserves vs liabilities in USDC)."""

import ast
import pathlib
import re
from unittest.mock import AsyncMock, patch

import pytest

from web import metrics

REPO = pathlib.Path(__file__).resolve().parent.parent
_METRIC_NAME_RE = re.compile(r"\b(tipbot_[a-z0-9_]+)\b")


@pytest.fixture(autouse=True)
def _reset_cache():
    metrics._metrics_cache = None
    metrics._metrics_ts = 0.0
    yield
    metrics._metrics_cache = None
    metrics._metrics_ts = 0.0


def _parse(out: str) -> dict:
    d = {}
    for line in out.splitlines():
        if line and not line.startswith("#"):
            name, _, val = line.partition(" ")
            d[name] = float(val)
    return d


def _make_env(hot: float | None):
    mock_ledger = MagicMockishLedger()
    mock_base = AsyncMock()
    if hot is None:
        mock_base.hot_balance = AsyncMock(side_effect=Exception("RPC down"))
    else:
        mock_base.hot_balance = AsyncMock(return_value=hot)
    mock_config = MockConfig(VAULT_ADDRESS="", USDC_DECIMALS=6)
    return mock_ledger, mock_base, mock_config


class MockConfig:
    def __init__(self, VAULT_ADDRESS, USDC_DECIMALS):
        self.VAULT_ADDRESS = VAULT_ADDRESS
        self.USDC_DECIMALS = USDC_DECIMALS


class MagicMockishLedger:
    total_liabilities = AsyncMock(return_value=60_000_000)
    open_markets = AsyncMock(return_value=[])
    global_stats = AsyncMock(return_value={"users": 2, "volume_micro": 10_000_000, "tips_micro": 4_000_000, "deposits_micro": 10_000_000})


@pytest.mark.asyncio
async def test_metrics_solvent_units():
    """hot_balance returns float USDC (100.0); liabilities 60 USDC -> solvent."""
    ledger, base, config = _make_env(hot=100.0)
    with patch("web.metrics.ledger", ledger), patch("web.metrics.base", base), patch("web.metrics.config", config):
        parsed = _parse(await metrics.collect_metrics())
    assert parsed["tipbot_liabilities_usdc"] == 60.0
    assert parsed["tipbot_reserves_usdc"] == 100.0
    assert parsed["tipbot_solvent"] == 1.0


@pytest.mark.asyncio
async def test_metrics_insolvent_when_reserves_low():
    """Regression: old code divided dollar reserves by 1e6, solvent was always 0."""
    ledger, base, config = _make_env(hot=40.0)
    with patch("web.metrics.ledger", ledger), patch("web.metrics.base", base), patch("web.metrics.config", config):
        parsed = _parse(await metrics.collect_metrics())
    assert parsed["tipbot_reserves_usdc"] == 40.0
    assert parsed["tipbot_solvent"] == 0.0


# ---------- the surface a scrape sees is the surface the code and the docs name ----------


def _emitted_names() -> set[str]:
    """The sample lines of one full payload — what Prometheus actually stores.

    `_format_metrics({})` is called with an empty dict on purpose: every value falls
    back to its default, so the shape of the exposition is read without touching a
    database or an RPC, and a metric whose line is deleted disappears from this set.
    """
    payload = metrics._format_metrics({})
    return {
        line.split(" ", 1)[0]
        for line in payload.splitlines()
        if line and not line.startswith("#")
    }


def _metric_names_in(text: str) -> set[str]:
    return set(_METRIC_NAME_RE.findall(text))


def _collector_keys(function: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> set[str]:
    """The `m[...]`/`m.get(...)` string keys one function names, by AST shape.

    `kind="write"` takes subscript assignment targets, `kind="read"` the first
    argument of a `.get`. A regex over the source would count the metric names in the
    `f"tipbot_x {m.get('x')}"` line as a write, which is exactly the confusion this
    law exists to end.
    """
    written: set[str] = set()
    for node in ast.walk(function):
        if kind == "write":
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "m"
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)
                    ):
                        written.add(target.slice.value)
        else:
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "m"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                written.add(node.args[0].value)
    return written


def _metrics_functions():
    tree = ast.parse((REPO / "web/metrics.py").read_text(encoding="utf-8-sig"), filename="web/metrics.py")
    by_name = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for needed in ("collect_metrics", "_format_metrics"):
        assert needed in by_name, f"`{needed}` is no longer a top-level function of web/metrics.py"
    return by_name["collect_metrics"], by_name["_format_metrics"]


def test_the_collector_writes_exactly_what_the_formatter_reads():
    """A figure the collector computes and the exposition never prints is a lie by
    omission — the operator reads the missing gauge as no data — and it is also work
    no scrape uses. `web/metrics.py` used to carry two of them (`tips_usdc`,
    `deposits_usdc`): `global_stats()` answered them, the collector divided them into
    USDC, and no line of `_format_metrics` named them.

    The floor of six is the current surface; it refuses a scan that went blind rather
    than a number anyone wants to preserve.
    """
    collect, fmt = _metrics_functions()
    written = _collector_keys(collect, "write")
    read = _collector_keys(fmt, "read")
    assert len(read) >= 6, f"the formatter names only {sorted(read)} — the reader went blind"
    assert not read - written, f"printed from a key nothing writes: {sorted(read - written)}"
    assert not written - read, f"collected and thrown away: {sorted(written - read)}"


def test_the_module_docstring_promises_the_names_the_payload_carries():
    """The docstring is the first thing a human integrating this endpoint reads, and it
    promised `tipbot_deposits_total`, `tipbot_withdrawals_total` and
    `tipbot_rpc_latency_seconds` — none of which any version of this file ever
    printed — while staying silent about `tipbot_volume_usdc`, which it did.
    """
    promised = _metric_names_in((REPO / "web/metrics.py").read_text(encoding="utf-8-sig").split('"""')[1])
    emitted = _emitted_names()
    assert len(emitted) >= 6, sorted(emitted)
    assert promised == emitted, (
        f"docstring-only {sorted(promised - emitted)}, payload-only {sorted(emitted - promised)}"
    )


def test_the_runbook_offers_the_metrics_the_endpoint_answers():
    """`docs/OPERATIONS.md` is where the person writing the Prometheus job looks, so a
    bullet there for a gauge that does not exist becomes a panel reading no data, and a
    published gauge the runbook never mentions is a gauge nobody will alert on.
    """
    text = (REPO / "docs/OPERATIONS.md").read_text(encoding="utf-8-sig")
    start = text.find("Available metrics:")
    end = text.find("Example Prometheus config", start + 1)
    assert start != -1 and end > start, "the runbook no longer carries the metrics list this law reads"
    promised = _metric_names_in(text[start:end])
    emitted = _emitted_names()
    assert promised == emitted, (
        f"runbook-only {sorted(promised - emitted)}, endpoint-only {sorted(emitted - promised)}"
    )

