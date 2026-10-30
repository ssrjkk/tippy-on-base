"""Creator-token trades get their own accounting kinds.

`/buytoken` used to write no rows at all: it granted the shares and then tried
to debit with an arity `Ledger.debit` does not have, so the command raised after
the grant was committed. The atomic replacement books two rows — `token_buy`
(buyer) and `token_sale` (the creator who receives the price) — and the closed
vocabulary from 011 rejects them until this revision widens it.

Revision ID: 017
Revises: 016
Create Date: 2026-10-30
"""
from collections.abc import Sequence

from alembic import op

revision: str = "017"
down_revision: str | Sequence[str] | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "bet", "bet_cancel", "bet_win", "channel_earn", "channel_pay", "creator_income",
    "deposit", "dividend", "fee", "market_buy", "market_cancel", "market_create",
    "market_sell", "market_win", "paywall", "paywall_earn", "rain_in", "tip",
    "tip_in", "token_buy", "token_sale", "withdraw", "withdraw_refund", "x402",
)


def _quote(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def upgrade() -> None:
    op.execute("ALTER TABLE tx_log DROP CONSTRAINT IF EXISTS chk_tx_log_kind;")
    op.execute(
        f"ALTER TABLE tx_log ADD CONSTRAINT chk_tx_log_kind CHECK (kind IN ({_quote(KINDS)}))"
    )


def downgrade() -> None:
    # Rows of the new kinds would violate the 011 vocabulary; they are derived
    # from a trade that no longer has a valid label, so they go with it.
    op.execute("DELETE FROM tx_log WHERE kind IN ('token_buy', 'token_sale');")
    op.execute("ALTER TABLE tx_log DROP CONSTRAINT IF EXISTS chk_tx_log_kind;")
    op.execute(
        "ALTER TABLE tx_log ADD CONSTRAINT chk_tx_log_kind CHECK (kind IN ("
        + _quote(tuple(k for k in KINDS if k not in ("token_buy", "token_sale")))
        + "))"
    )
