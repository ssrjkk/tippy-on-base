"""One definition of the win fee, shared by the ledger and by every preview.

`resolve_bet` ceiled the fee on a Decimal while `/mybets` truncated the same
product with `int(profit * WIN_FEE_PCT)`, so the screen promised a net one micro
higher than the credit for any profit whose 2% is not a whole micro; and four
copies of the formula meant a rate change had four places to be missed.
"""

from decimal import ROUND_CEILING, Decimal

from bot import config


def win_fee_micro(profit_micro: int) -> int:
    """Fee on a winning payout: WIN_FEE_PCT of the profit, rounded up.

    Rounding up is the conservative direction for the platform and the one the
    ledger has always used; a non-positive profit (a losing or break-even share
    of the pot) pays nothing.
    """
    if profit_micro <= 0:
        return 0
    return int((Decimal(profit_micro) * config.WIN_FEE_PCT).to_integral_value(rounding=ROUND_CEILING))


def win_fee_pct_label() -> str:
    """The rate as exact display text — `int(WIN_FEE_PCT * 100)` would print a
    2.5% fee as "2%"."""
    return f"{(config.WIN_FEE_PCT * 100).normalize():f}"
