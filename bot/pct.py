"""The one place an odds percentage is computed and rounded.

Every surface in this product paints a share, a chance or a price at one decimal
(`fmtPct` in `app.js`/`market.html`/`oc.html`, `pctLabel` in `site.js`/`app.html`),
so one decimal is the precision the number has to exist at. A producer that
publishes more does not show the user an extra digit — it hands the value to
`toLocaleString`, which rounds it a second time with a different rule (ICU
rounds half-to-even, this product rounds half-up), so one option could read
`12,3%` on the card and `12,4%` on its own page. Producers here round once,
half-up, at the face the page paints.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# The painted odds face. Nothing may quantize an odds figure at another
# precision: `pctLabel`/`fmtPct` state 1 minimum and 1 maximum fraction digit,
# and `tests/test_design_system.py` holds the two numbers equal.
ODDS_DP = 1

_STEP = Decimal(1).scaleb(-ODDS_DP)  # Decimal("0.1")

Number = int | float | str | Decimal


def _decimal(value: Number) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # str() gives the shortest representation that reads back as the same
        # float, which is the number the caller actually holds. Decimal(float)
        # would take the binary expansion instead and round 0.12349999999999999
        # to a figure nobody wrote.
        return Decimal(str(value))
    return Decimal(value)


def _quantize(percent: Decimal) -> float:
    return float(percent.quantize(_STEP, rounding=ROUND_HALF_UP))


def share(part: Number, whole: Number) -> float:
    """`part` of `whole` as the percentage the page prints.

    One division and one rounding: a pool split and a pot are micro integers,
    and `(part / whole) * 100` in floats would round in the division before
    rounding again at the face. An empty whole is zero, not a crash — every
    caller that showed `0.0` for an empty pot now shows what this returns.
    """
    whole_d = _decimal(whole)
    if not whole_d:
        return 0.0
    return _quantize(_decimal(part) / whole_d * 100)


def as_pct(ratio: Number) -> float:
    """A ratio already in 0..1 — an LMSR or on-chain price — as the printed percent."""
    return _quantize(_decimal(ratio) * 100)


def from_pct(percent: Number) -> float:
    """A figure already stated in percent, brought to the printed face.

    The pair of `as_pct`: a field the API publishes as `price_pct` is a percent,
    not a ratio, and a surface that repeats it must not multiply it again. It
    exists because a number that has already been rounded still has to be
    written into a sentence, and the sentence's own format spec
    (`f"{price:.1f}%"`) is a second rounder — the builtin's, over a float, with
    a different rule from the one that produced the value. One call, one face.
    """
    return _quantize(_decimal(percent))
