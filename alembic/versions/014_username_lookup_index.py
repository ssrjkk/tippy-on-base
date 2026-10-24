"""An index the username lookup can actually use.

`idx_users_username` (001) indexes the raw column, while `find_by_username`
matches `LOWER(username) = LOWER($1)` — a btree on the expression on the left
does not exist, so the planner Seq Scans `users` for every "/tip @someone",
channel resolution and leaderboard name lookup. Proven with
`SET enable_seqscan = off`: the query still chose the Seq Scan.

The lookup cannot drop case-insensitivity (Telegram usernames are
case-insensitive and stored as the user last sent them), so the index is the
thing that changes.

Revision ID: 014
Revises: 013
Create Date: 2026-10-24
"""
from collections.abc import Sequence

from alembic import op

revision: str = "014"
down_revision: str | Sequence[str] | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_username_lower ON users (LOWER(username));"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_username_lower;")
