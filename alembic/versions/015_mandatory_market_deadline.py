"""Every market has a deadline.

`bets.close_at` and `markets.close_at` were nullable, and NULL meant "no
deadline" — which is how money got parked: the deadline watcher and the grace
warning both filtered on `close_at IS NOT NULL`, so an open-ended market was
never pinged, never expired and never became refundable by anyone but its
creator. The creator stayed an oracle with no clock and the backers' USDC had no
path out. `onchain_markets.close_at` was already NOT NULL; the two older tables
were the outliers.

Open rows get the window a market gets today — `created_at + 7 days`. For a
market created long ago that deadline has passed, which is the intended
outcome: after the grace period anyone may refund it.

The default is spelled out as 604800 instead of read from `bot.config` because a
migration has to replay the same way on every host and in the future, whatever
MARKET_DEFAULT_HOURS is set to then. New rows get the value from config in
`create_bet`/`create_market`; the NOT NULL that follows is what stops the NULL
state from coming back.

Revision ID: 015
Revises: 014
Create Date: 2026-10-24
"""
from collections.abc import Sequence

from alembic import op

revision: str = "015"
down_revision: str | Sequence[str] | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_WINDOW_SECONDS = 7 * 86400


def upgrade() -> None:
    for table in ("bets", "markets"):
        op.execute(
            f"UPDATE {table} SET close_at = created_at + {_DEFAULT_WINDOW_SECONDS} "
            "WHERE close_at IS NULL;"
        )
        op.execute(f"ALTER TABLE {table} ALTER COLUMN close_at SET NOT NULL;")


def downgrade() -> None:
    # The backfill stays: turning the column nullable again does not un-set the
    # deadlines, and dropping them would put the parked-money problem back.
    for table in ("bets", "markets"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN close_at DROP NOT NULL;")
