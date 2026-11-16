"""Treasury + AML tables — the first time the chain had to catch the runtime up.

The initial schema (001) predated the community-treasury and AML-monitor
features. The runtime DDL string created these tables at startup, but any
deploy path that provisions the database via `alembic upgrade head` was missing
them and crashed the treasury flows / AML monitor. This migration is one of the
attempts to make the two schema sources agree; 018 is the one that ended the
arrangement by removing the second source.

Measured in the same wave: `treasury_transactions`, `treasury_proposals` and
`treasury_votes` — the voting half of a community treasury the bot never grew —
are named by no SQL any process executes and hold zero rows in every database
here. `community_treasuries` (the balance the solvency sum counts) is live. The
three are left in place because dropping them is a destructive change to live
schemas; `tests/test_ledger.py::test_the_chain_creates_no_table_the_runtime_never_reads`
pins them so the list cannot grow meanwhile.

Revision ID: 002
Revises: 001
"""
from collections.abc import Sequence

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS suspicious_activity (
            id          BIGSERIAL PRIMARY KEY,
            tg_id       BIGINT NOT NULL,
            kind        TEXT NOT NULL,
            details     TEXT NOT NULL,
            severity    TEXT NOT NULL DEFAULT 'info',
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE INDEX IF NOT EXISTS idx_suspicious_tg ON suspicious_activity (tg_id);
        CREATE INDEX IF NOT EXISTS idx_suspicious_created ON suspicious_activity (created_at);

        CREATE TABLE IF NOT EXISTS community_treasuries (
            id          BIGSERIAL PRIMARY KEY,
            chat_id     BIGINT NOT NULL UNIQUE,
            owner_tg    BIGINT NOT NULL,
            balance     BIGINT NOT NULL DEFAULT 0,
            quorum_pct  INTEGER NOT NULL DEFAULT 50,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS treasury_transactions (
            id          BIGSERIAL PRIMARY KEY,
            treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
            kind        TEXT NOT NULL,
            tg_id       BIGINT,
            amount      BIGINT NOT NULL,
            note        TEXT,
            tx_hash     TEXT,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        CREATE TABLE IF NOT EXISTS treasury_proposals (
            id          BIGSERIAL PRIMARY KEY,
            treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
            proposer_tg BIGINT NOT NULL,
            amount      BIGINT NOT NULL,
            to_address  TEXT NOT NULL,
            description TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'voting',
            votes_yes   INTEGER NOT NULL DEFAULT 0,
            votes_no    INTEGER NOT NULL DEFAULT 0,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
            closes_at   BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS treasury_votes (
            id          BIGSERIAL PRIMARY KEY,
            treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
            proposal_id BIGINT NOT NULL,
            tg_id       BIGINT NOT NULL,
            vote        INTEGER NOT NULL,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
            UNIQUE (proposal_id, tg_id)
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS treasury_votes;
        DROP TABLE IF EXISTS treasury_proposals;
        DROP TABLE IF EXISTS treasury_transactions;
        DROP TABLE IF EXISTS community_treasuries;
        DROP TABLE IF EXISTS suspicious_activity;
    """)
