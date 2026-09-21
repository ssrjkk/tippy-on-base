"""CHECK constraints on balance and shares columns.

Defense-in-depth: prevent negative balances at the DB level so that a buggy
code path cannot mint money from nothing or create negative share positions.

Revision ID: 006
Revises: 005
Create Date: 2026-09-21
"""
from typing import Sequence

from alembic import op

revision: str = "006"
down_revision: str | Sequence[str] | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_users_balance_nn') THEN
                ALTER TABLE users ADD CONSTRAINT chk_users_balance_nn CHECK (balance >= 0);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_market_shares_nn') THEN
                ALTER TABLE market_shares ADD CONSTRAINT chk_market_shares_nn CHECK (shares >= 0);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_treasury_balance_nn') THEN
                ALTER TABLE community_treasuries ADD CONSTRAINT chk_treasury_balance_nn CHECK (balance >= 0);
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE community_treasuries DROP CONSTRAINT IF EXISTS chk_treasury_balance_nn;")
    op.execute("ALTER TABLE market_shares DROP CONSTRAINT IF EXISTS chk_market_shares_nn;")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_users_balance_nn;")
