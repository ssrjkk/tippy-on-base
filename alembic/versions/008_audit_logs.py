"""Audit log table for critical operations.

Tracks all sensitive actions (withdrawals, deposits, admin actions) for
compliance, security investigations, and chargeback resolution.

Revision ID: 008
Revises: 007
Create Date: 2026-10-01
"""
from collections.abc import Sequence

from alembic import op

revision: str = "008"
down_revision: str | Sequence[str] | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id          BIGSERIAL PRIMARY KEY,
            created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
            user_id     BIGINT,
            action      TEXT NOT NULL,
            resource    TEXT NOT NULL,
            metadata    JSONB NOT NULL DEFAULT '{}',
            ip_address  TEXT,
            success     BOOLEAN NOT NULL DEFAULT TRUE
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_created
            ON audit_logs (created_at DESC);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_user_action
            ON audit_logs (user_id, action);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_action
            ON audit_logs (action);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_logs;")
