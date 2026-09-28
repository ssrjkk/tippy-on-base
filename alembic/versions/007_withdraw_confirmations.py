"""Two-step withdrawal confirmation staging table.

/withdraw stages the payout here and nothing moves until the user taps
Confirm. One live stage per user (staging replaces the previous one) and the
TTL is enforced by the reader, so no cleanup job is required.

Revision ID: 007
Revises: 006
Create Date: 2026-09-25
"""
from typing import Sequence

from alembic import op

revision: str = "007"
down_revision: str | Sequence[str] | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS withdraw_confirmations (
            token        TEXT PRIMARY KEY,
            tg_id        BIGINT NOT NULL,
            to_address   TEXT NOT NULL,
            amount_micro BIGINT NOT NULL,
            fee_micro    BIGINT NOT NULL,
            created_at   BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_wd_confirm_tg
            ON withdraw_confirmations (tg_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS withdraw_confirmations;")
