"""Add missing indexes for query performance and idempotency table.

Also materializes onchain_markets/onchain_trades, which previously existed
only in runtime DDL (bot/ledger/_schema.py) — on a fresh database
`alembic upgrade head` crashed in this revision because the tables were
missing at index-creation time.

Covers high-traffic lookups that were doing sequential scans:
- tx_log.tx_hash (deposit reconciliation)
- tx_log.user_id (user transaction history)
- x402_payments recipient/sender (payment lookups)
- pending_deposits.tg_id (claim matching)
- onchain_trades.market_id (trade history per market)

Also adds idempotency_keys table for money endpoint replay protection.

Revision ID: 009
Revises: 008
Create Date: 2026-10-03
"""
from collections.abc import Sequence

from alembic import op

revision: str = "009"
down_revision: str | Sequence[str] | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tx_log_tx_hash
            ON tx_log (tx_hash)
            WHERE tx_hash IS NOT NULL;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tx_log_user_created
            ON tx_log (tg_id, created_at DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_x402_payments_recipient
            ON x402_payments (recipient_tg);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_x402_payments_sender
            ON x402_payments (sender);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_pending_deposits_tg
            ON pending_deposits (LOWER(sender));
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS onchain_markets (
            id         BIGINT PRIMARY KEY,               -- on-chain OutcomeMarket marketId
            creator    BIGINT NOT NULL,
            question   TEXT NOT NULL,
            options    TEXT NOT NULL,                    -- JSON array (labels live off-chain)
            close_at   BIGINT NOT NULL,
            created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
        ALTER TABLE onchain_markets ADD COLUMN IF NOT EXISTS deadline_notified BIGINT NOT NULL DEFAULT 0;
        ALTER TABLE onchain_markets ADD COLUMN IF NOT EXISTS resolved_outcome BIGINT;
        ALTER TABLE onchain_markets ADD COLUMN IF NOT EXISTS cancelled_flag BIGINT NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_onchain_markets_close ON onchain_markets (close_at);
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS onchain_trades (
            id         BIGSERIAL PRIMARY KEY,
            market_id  BIGINT NOT NULL,
            tg_id      BIGINT NOT NULL,
            outcome    BIGINT NOT NULL,
            shares     BIGINT NOT NULL,              -- micro-shares bought (at buy time)
            tx_hash    TEXT,
            created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_onchain_trades_user
            ON onchain_trades (tg_id, created_at DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_onchain_trades_market
            ON onchain_trades (market_id, outcome);
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key_hash    TEXT PRIMARY KEY,
            user_id     BIGINT NOT NULL,
            action      TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            result_id   TEXT,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
            expires_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint + 86400)
        );
        CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency_keys (expires_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS idempotency_keys;")
    op.execute("DROP INDEX IF EXISTS idx_onchain_trades_market;")
    op.execute("DROP INDEX IF EXISTS idx_onchain_trades_user;")
    op.execute("DROP INDEX IF EXISTS idx_pending_deposits_tg;")
    op.execute("DROP INDEX IF EXISTS idx_x402_payments_sender;")
    op.execute("DROP INDEX IF EXISTS idx_x402_payments_recipient;")
    op.execute("DROP INDEX IF EXISTS idx_tx_log_user_created;")
    op.execute("DROP INDEX IF EXISTS idx_tx_log_tx_hash;")
