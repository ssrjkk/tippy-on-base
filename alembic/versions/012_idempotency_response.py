"""Give the idempotency key an answer.

`reserve_idempotency` wrote a row and the routes stored `status`/`result_id`,
but nothing ever read them back: a second request with the same key got a bare
409, so a client whose connection dropped after sending a tip could not learn
whether the money moved, and the advice in that 409 ("reuse the original key to
check status") described an endpoint that does not exist.

`response` holds the JSON body the first attempt returned, which is what turns
"409 forever" into a replay of the original outcome.

Revision ID: 012
Revises: 011
Create Date: 2026-10-23
"""
from collections.abc import Sequence

from alembic import op

revision: str = "012"
down_revision: str | Sequence[str] | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE idempotency_keys ADD COLUMN IF NOT EXISTS response TEXT;")


def downgrade() -> None:
    op.execute("ALTER TABLE idempotency_keys DROP COLUMN IF EXISTS response;")
