"""The idempotency key's states, enforced.

A reservation used to be either 'pending' or 'completed', and 'pending' meant
two different things depending on which code path wrote it: "the transaction is
open" and "the attempt died before it stored anything". The second reading is
what made an expiry sweep necessary — and that sweep is exactly how a committed
but unstored tip became spendable again under the same key.

Now the reservation is written inside the money transaction, so the states mean
one thing each: 'pending' = open right now (a rollback removes the row, so a
crash leaves no trace), 'applied' = the balances moved, 'completed' = and the
response body is stored for the next duplicate to be answered with. Only
'completed' rows ever age out.

Revision ID: 013
Revises: 012
Create Date: 2026-10-24
"""
from collections.abc import Sequence

from alembic import op

revision: str = "013"
down_revision: str | Sequence[str] | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_idempotency_status') THEN
                ALTER TABLE idempotency_keys
                    ADD CONSTRAINT chk_idempotency_status
                    CHECK (status IN ('pending', 'applied', 'completed'));
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE idempotency_keys DROP CONSTRAINT IF EXISTS chk_idempotency_status;")
    # 'applied' is the state that only exists in this model; folding it back
    # into 'completed' keeps the row honest about the money having moved.
    op.execute("UPDATE idempotency_keys SET status = 'completed' WHERE status = 'applied';")
