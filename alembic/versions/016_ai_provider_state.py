"""Where the AI assistant keeps its state: provider health and the daily budget.

Both used to live in module dicts. The daily answer counter was the worse of
the two: uvicorn, the bot and the agent are separate processes, so a
process-local counter let each of them serve ``AI_DAILY_ANSWERS`` answers, and
every one of them restarted the budget to zero on deploy. Provider health is new
but belongs in the same place — a parked provider has to survive a restart,
otherwise a dead key is re-probed on the very first question after every deploy.

``ai_provider.key_env`` stores the NAME of the environment variable holding the
API key, never the key: this row is read by the dashboard and dumped by backups.

Revision ID: 016
Revises: 015
Create Date: 2026-10-25
"""
from collections.abc import Sequence

from alembic import op

revision: str = "016"
down_revision: str | Sequence[str] | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_provider (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            url           TEXT NOT NULL,
            model         TEXT NOT NULL,
            key_env       TEXT NOT NULL,
            fail_count    INT NOT NULL DEFAULT 0 CHECK (fail_count >= 0),
            parked_until  TIMESTAMPTZ,
            last_error    TEXT,
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_usage_day (
            day       DATE PRIMARY KEY,
            answers   INT NOT NULL DEFAULT 0 CHECK (answers >= 0)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_usage_day;")
    op.execute("DROP TABLE IF EXISTS ai_provider;")
