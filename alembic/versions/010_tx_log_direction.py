"""Make tx_log rows say which way the money moved.

`amount` is always positive, so the sign of a row has to come from `kind`, and
two kinds broke that rule:

- A rain wrote one debit row for the sender plus one `tip` row per recipient.
  Every `kind = 'tip'` aggregate then read the recipient's credit as if that
  recipient had tipped it away: rain recipients climbed the tipper leaderboard,
  `tips_sent` for a pure receiver was non-zero, the sender was credited with
  receiving its own rain, and the shares were counted into volume twice.
- Creator income (a market's 2% win fee, and a cancelled market's leftover
  subsidy) was written as `fee` — the same kind a withdrawal fee debits the
  payer with, so it showed up as a refund in one direction and as protocol fees
  in the global totals.

Rain recipients become `rain_in`, creator income becomes `creator_income`, and a
cancelled market's subsidy return becomes the `market_cancel` refund it is.

Existing rows are rewritten from the pairing left in the data: the sender row
names its recipients in `counterparty` and is inserted first, so a `tip`/`rain`
row whose own `counterparty` is that sender is a recipient row. A sender row
that names several recipients never matches, because its `counterparty` is a
comma list; the one case this cannot tell apart is two opposite one-recipient
rains of the same pair, which the table does not record any other way.

Revision ID: 010
Revises: 009
Create Date: 2026-10-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "010"
down_revision: str | Sequence[str] | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        UPDATE tx_log r
           SET kind = 'rain_in'
          FROM tx_log s
         WHERE r.kind = 'tip' AND r.note = 'rain'
           AND s.kind = 'tip' AND s.note = 'rain' AND s.id < r.id
           AND s.tg_id::text = r.counterparty
           AND (s.counterparty = r.tg_id::text
                OR ',' || s.counterparty || ',' LIKE '%,' || r.tg_id::text || ',%');
    """)
    op.execute("""
        UPDATE tx_log SET kind = 'creator_income'
         WHERE kind = 'fee' AND note = 'market fees';
    """)
    op.execute("""
        UPDATE tx_log SET kind = 'market_cancel'
         WHERE kind = 'fee' AND note = 'market subsidy back';
    """)


def downgrade() -> None:
    op.execute("UPDATE tx_log SET kind = 'fee', note = 'market fees' WHERE kind = 'creator_income';")
    op.execute(
        "UPDATE tx_log SET kind = 'fee', note = 'market subsidy back' "
        "WHERE kind = 'market_cancel' AND note = 'market subsidy back';"
    )
    op.execute("UPDATE tx_log SET kind = 'tip' WHERE kind = 'rain_in';")
