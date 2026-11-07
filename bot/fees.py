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


def rate_label(rate: Decimal) -> str:
    """A rate as the exact text a screen shows — `f'{rate * 100:.0f}'` prints a
    2.5% fee as "2%", and a preview that understates a fee is a broken promise."""
    return f"{(Decimal(rate) * 100).normalize():f}"


def usdc_label(amount: Decimal) -> str:
    """A USDC amount with no trailing zeros and never in exponent notation."""
    return f"{Decimal(amount).normalize():f}"


def win_fee_pct_label() -> str:
    return rate_label(config.WIN_FEE_PCT)


def withdraw_fee_pct_label() -> str:
    return rate_label(config.WITHDRAW_FEE_PCT)
