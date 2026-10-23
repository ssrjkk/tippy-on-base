"""Close the tx_log vocabulary and give every tip a receiver-side row.

`kind` is the accounting primitive of this ledger: the sign, the label and the
counterparty of a row all come from it, and `amount` is always positive. Three
things did not respect that:

- A direct tip wrote one row (tg_id = payer). The receiver's /history had no
  entry for money /stats reported as theirs, so the two surfaces disagreed.
- A reaction tip wrote its single row under the AUTHOR — the person being paid —
  so their income rendered as money they had tipped away, and the payer had no
  row at all.
- `/claim` credited dividends with a prose kind ("Dividends from 7"), which
  `is_credit()` cannot know, so a payout rendered as a debit with a missing
  label. It writes `dividend` now.
- A paywall or channel purchase wrote `amount = -price`, the one place that
  broke "amount is always positive", so the renderer printed a double minus.

Receiver rows are rebuilt from the pairing the table already holds: a `tip` row
whose counterparty is one Telegram id is a direct tip, and `note = 'reaction'`
marks the rows that were filed under the wrong side (same shape as 010's rain
repair). Two opposite tips of the same amount within the same second cannot be
told apart from their mirror, so one of them may stay un-mirrored; that costs a
history line, never money.

`status = 'confirmed'` was written by nothing in the code base — seed data only
— so it becomes the terminal `done` it was meant to say. The vocabulary is then
closed with CHECK constraints: kind, status, and amount > 0.

Revision ID: 011
Revises: 010
Create Date: 2026-10-23
"""
from collections.abc import Sequence

from alembic import op

revision: str = "011"
down_revision: str | Sequence[str] | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "bet", "bet_cancel", "bet_win", "channel_earn", "channel_pay", "creator_income",
    "deposit", "dividend", "fee", "market_buy", "market_cancel", "market_create",
    "market_sell", "market_win", "paywall", "paywall_earn", "rain_in", "tip",
    "tip_in", "withdraw", "withdraw_refund", "x402",
)
STATUSES = ("queued", "pending", "done", "refunded")


def _check(name: str, predicate: str) -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
                ALTER TABLE tx_log ADD CONSTRAINT {name} CHECK ({predicate});
            END IF;
        END $$;
    """)


def _quote(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def upgrade() -> None:
    op.execute("""
        UPDATE tx_log SET kind = 'tip_in'
         WHERE kind = 'tip' AND note = 'reaction';
    """)
    # The payer of a reaction tip never got their debit row.
    op.execute("""
        INSERT INTO tx_log (kind, tg_id, counterparty, amount, note, created_at)
        SELECT 'tip', t.counterparty::bigint, t.tg_id::text, t.amount, 'reaction', t.created_at
          FROM tx_log t
         WHERE t.kind = 'tip_in' AND t.note = 'reaction' AND t.counterparty ~ '^[0-9]+$'
           AND NOT EXISTS (
                SELECT 1 FROM tx_log x
                 WHERE x.kind = 'tip' AND x.note = 'reaction'
                   AND x.tg_id = t.counterparty::bigint
                   AND x.counterparty = t.tg_id::text
                   AND x.amount = t.amount AND x.created_at = t.created_at);
    """)
    # Every direct tip gets its receiver-side credit row.
    op.execute("""
        INSERT INTO tx_log (kind, tg_id, counterparty, amount, note, created_at)
        SELECT 'tip_in', t.counterparty::bigint, t.tg_id::text, t.amount, t.note, t.created_at
          FROM tx_log t
         WHERE t.kind = 'tip' AND t.note IS DISTINCT FROM 'rain'
           AND t.note IS DISTINCT FROM 'reaction' AND t.counterparty ~ '^[0-9]+$'
           AND NOT EXISTS (
                SELECT 1 FROM tx_log x
                 WHERE x.kind = 'tip_in'
                   AND x.tg_id = t.counterparty::bigint
                   AND x.counterparty = t.tg_id::text
                   AND x.amount = t.amount AND x.created_at = t.created_at);
    """)
    op.execute("""
        UPDATE tx_log SET status = 'done'
         WHERE status IS NOT NULL
           AND status NOT IN ('queued', 'pending', 'done', 'refunded');
    """)
    # A paywall/channel purchase stored its debit as a negative amount, so the
    # history rendered it as '-−0.4'. The sign belongs to the kind: fold the
    # magnitude back into the positive value the invariant says every row holds.
    op.execute("UPDATE tx_log SET amount = -amount WHERE amount < 0;")
    _check("chk_tx_log_kind", f"kind IN ({_quote(KINDS)})")
    _check("chk_tx_log_status", f"status IS NULL OR status IN ({_quote(STATUSES)})")
    _check("chk_tx_log_amount_positive", "amount > 0")


def downgrade() -> None:
    op.execute("ALTER TABLE tx_log DROP CONSTRAINT IF EXISTS chk_tx_log_amount_positive;")
    op.execute("ALTER TABLE tx_log DROP CONSTRAINT IF EXISTS chk_tx_log_status;")
    op.execute("ALTER TABLE tx_log DROP CONSTRAINT IF EXISTS chk_tx_log_kind;")
    # The mirrored rows are derived, not source data: dropping them is lossless,
    # and re-applying upgrade() rebuilds them from the payer rows. A prose
    # dividend kind cannot be restored — it was never a valid accounting label.
    op.execute("DELETE FROM tx_log WHERE kind = 'tip_in';")
    op.execute("DELETE FROM tx_log WHERE kind = 'tip' AND note = 'reaction';")
