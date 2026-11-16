"""The tracked chain builds every table the runtime writes to.

Measured, not assumed: a database created by `alembic upgrade head` alone was
compared, object by object, with one created by the idempotent DDL string that
`ensure_schema()` applied after it. The two disagreed in both directions, and the
direction that mattered was the one nobody could see, because every process run
applied the DDL string on top of the chain and hid the gap:

* the chain never created `recurring_payments`, `x402_invoices`, `login_nonces`,
  `paymaster_usage`, `gas_drips`, `market_subsidies`, `notification_outbox`,
  `creator_tokens`, `creator_token_holders`, `creator_dividends` — subscriptions,
  the x402 paywall, wallet-login replay protection, the gasless allowance counter
  and revenue sharing all had no table at all;
* the chain never created `users.smart_address`, `users.smart_deployed`,
  `users.smart_created_at`, `pending_deposits.block`, `pending_deposits.created_at`
  or `x402_payments.pay_to` — so a fresh install could not resolve a Smart Account,
  could not record a deposit's block, and the scanner had no timestamp to order by;
* `bets.close_at` and `markets.close_at` were NOT NULL in the chain (015) and still
  nullable in the DDL string, so the DDL path let a deadline-less market — the parked
  money 015 exists to prevent — be created again;
* `idx_bets_status`, `idx_markets_status` and `idx_pending_deposits_claimed` existed
  only in the DDL string: an open-market list and the deposit scanner's `claimed = 0`
  poll had no index to use;
* `chk_ai_usage_day_nn` and `chk_ai_provider_fails_nn` existed only in the DDL string.

The other direction was just as live: `audit_logs` and `idempotency_keys` — with
`chk_idempotency_status`, the table the money routes reserve into and the table the
audit trail writes — existed only in the chain. Whenever alembic raised (the nested
`EnvironmentContext` `KeyError: 'config'` is the documented case) `_run_alembic()`
logged, swallowed, and `ensure_schema()` built the DDL world: a process running with
no `idempotency_keys` table, refusing every money `POST` with a database error, and
no `audit_logs` to record what it did.

So this revision makes the chain complete, and the DDL string goes away with it.
Everything below is idempotent — a database that already has an object is left alone,
which is the shape of every live database here, since they were all built by chain
plus DDL. Object *names* match what `CREATE TABLE … PRIMARY KEY` and `UNIQUE`
auto-generate, so this migration converges the two names onto one instead of adding
a third.

Revision ID: 018
Revises: 017
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "018"
down_revision: str | Sequence[str] | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Verbatim from the DDL string this revision replaces, so the database a fresh
# install gets is the database a running process has always had.
_TABLES = (
    """CREATE TABLE IF NOT EXISTS login_nonces (
           nonce_hash TEXT PRIMARY KEY,
           created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
       )""",
    """CREATE TABLE IF NOT EXISTS paymaster_usage (
           address     TEXT PRIMARY KEY,
           used_count  BIGINT NOT NULL DEFAULT 0,
           last_used   BIGINT NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS gas_drips (
           day   BIGINT PRIMARY KEY,
           count BIGINT NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS market_subsidies (
           day         BIGINT PRIMARY KEY,
           total_micro BIGINT NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS notification_outbox (
           id            BIGSERIAL PRIMARY KEY,
           chat_id       BIGINT NOT NULL,
           text          TEXT NOT NULL,
           retries       INT NOT NULL DEFAULT 0,
           next_retry_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
           created_at    BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
       )""",
    """CREATE TABLE IF NOT EXISTS recurring_payments (
           id               TEXT PRIMARY KEY,
           from_tg_id       BIGINT NOT NULL,
           to_tg_id         BIGINT NOT NULL,
           amount_micro     BIGINT NOT NULL,
           "interval"       TEXT NOT NULL,
           memo             TEXT NOT NULL DEFAULT '',
           active           BOOLEAN NOT NULL DEFAULT true,
           created_at       BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
           next_execution   BIGINT NOT NULL,
           last_execution   BIGINT NOT NULL DEFAULT 0,
           execution_count  BIGINT NOT NULL DEFAULT 0,
           max_executions   BIGINT NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS x402_invoices (
           invoice_id   TEXT PRIMARY KEY,
           pay_addr     TEXT NOT NULL UNIQUE,
           recipient_tg BIGINT NOT NULL,
           amount_micro BIGINT NOT NULL,
           kind         TEXT NOT NULL,
           ref_id       TEXT,
           pay_to       TEXT,
           created_at   BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
           credited     BOOLEAN NOT NULL DEFAULT false,
           swept_at     BIGINT
       )""",
    """CREATE TABLE IF NOT EXISTS creator_tokens (
           token_id                 TEXT PRIMARY KEY,
           creator_tg_id            BIGINT NOT NULL,
           name                     TEXT NOT NULL,
           symbol                   TEXT NOT NULL,
           total_supply             BIGINT NOT NULL,
           price_micro              BIGINT NOT NULL,
           created_at               BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
           total_revenue_micro      BIGINT NOT NULL DEFAULT 0,
           total_dividends_paid_micro BIGINT NOT NULL DEFAULT 0,
           dividend_per_token_micro   DOUBLE PRECISION NOT NULL DEFAULT 0
       )""",
    """CREATE TABLE IF NOT EXISTS creator_token_holders (
           token_id     TEXT NOT NULL REFERENCES creator_tokens (token_id),
           holder_tg_id BIGINT NOT NULL,
           balance      BIGINT NOT NULL DEFAULT 0,
           pending_dividends_micro BIGINT NOT NULL DEFAULT 0,
           last_dividend_claim DOUBLE PRECISION NOT NULL DEFAULT 0,
           PRIMARY KEY (token_id, holder_tg_id),
           CONSTRAINT chk_creator_holders_balance_nn CHECK (balance >= 0 AND pending_dividends_micro >= 0)
       )""",
    """CREATE TABLE IF NOT EXISTS creator_dividends (
           id          BIGSERIAL PRIMARY KEY,
           token_id    TEXT NOT NULL,
           amount_micro BIGINT NOT NULL,
           dividend_per_token DOUBLE PRECISION NOT NULL,
           total_holders BIGINT NOT NULL,
           created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
       )""",
)

_COLUMNS = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS smart_address TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS smart_deployed BOOLEAN DEFAULT false",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS smart_created_at BIGINT",
    "ALTER TABLE pending_deposits ADD COLUMN IF NOT EXISTS block BIGINT",
    "ALTER TABLE pending_deposits "
    "ADD COLUMN IF NOT EXISTS created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)",
    "ALTER TABLE x402_payments ADD COLUMN IF NOT EXISTS pay_to TEXT",
)

# The three the DDL string had and the chain did not, plus the indexes of the
# tables created above. Single-column `tx_log (tg_id)` / `tx_log (kind)` /
# `users (username)` from the DDL string are deliberately NOT reproduced: the
# chain's `idx_tx_log_user_created (tg_id, created_at DESC)`,
# `idx_tx_log_kind_ct (kind, created_at)` and `idx_users_username_lower
# (lower(username))` already serve those reads, and a second index on a column
# the planner can reach through the leading edge of an existing one is write
# cost with no reader — `find_by_username` looks up `LOWER(username)`, never the
# raw column (see 014).
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_bets_status ON bets (status)",
    "CREATE INDEX IF NOT EXISTS idx_markets_status ON markets (status)",
    "CREATE INDEX IF NOT EXISTS idx_pending_deposits_claimed ON pending_deposits (claimed)",
    "CREATE INDEX IF NOT EXISTS idx_recurring_payments_due "
    "ON recurring_payments (next_execution) WHERE active = true",
    "CREATE INDEX IF NOT EXISTS idx_recurring_payments_from ON recurring_payments (from_tg_id)",
    "CREATE INDEX IF NOT EXISTS idx_recurring_payments_to ON recurring_payments (to_tg_id)",
    "CREATE INDEX IF NOT EXISTS idx_x402_invoices_credited ON x402_invoices (credited)",
    "CREATE INDEX IF NOT EXISTS idx_x402_invoices_recv ON x402_invoices (recipient_tg)",
    "CREATE INDEX IF NOT EXISTS idx_creator_tokens_creator ON creator_tokens (creator_tg_id)",
    "CREATE INDEX IF NOT EXISTS idx_creator_holders_holder ON creator_token_holders (holder_tg_id)",
    "CREATE INDEX IF NOT EXISTS idx_creator_dividends_token ON creator_dividends (token_id)",
)

# One fact, one name: the DDL string and the chain both declared these, under two
# names each. The chain's name survives, the duplicate goes.
_DEDUP = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_wallets_user_slot ON user_wallets (tg_id, slot)",
    "ALTER TABLE user_wallets DROP CONSTRAINT IF EXISTS user_wallets_tg_id_slot_key",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_create2_proxies_address "
    "ON create2_proxies (lower(proxy_address))",
    "DROP INDEX IF EXISTS idx_create2_proxies_addr",
    "CREATE INDEX IF NOT EXISTS idx_pending_deposits_tg ON pending_deposits (lower(sender))",
    "DROP INDEX IF EXISTS idx_pending_deposits_sender",
)

_CHECKS = (
    """DO $$ BEGIN
           IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_ai_usage_day_nn') THEN
               ALTER TABLE ai_usage_day ADD CONSTRAINT chk_ai_usage_day_nn CHECK (answers >= 0);
           END IF;
           IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_ai_provider_fails_nn') THEN
               ALTER TABLE ai_provider ADD CONSTRAINT chk_ai_provider_fails_nn CHECK (fail_count >= 0);
           END IF;
       END $$""",
)


_COMMENTS = (
    # The status domain used to be readable only from the DDL string this revision
    # deletes. On the database it is now stated in the catalog, so `\d bets` answers
    # the same vocabulary the migrations and the interface are held to.
    "COMMENT ON COLUMN bets.status IS 'open | resolved | cancelled'",
    "COMMENT ON COLUMN markets.status IS 'open | resolved | cancelled'",
)


def upgrade() -> None:
    for ddl in _TABLES + _COLUMNS + _INDEXES + _DEDUP + _CHECKS + _COMMENTS:
        op.execute(ddl)


def downgrade() -> None:
    # Rolling back would drop tables that hold money: schedules still due, invoices
    # not yet swept, tokens with holders and dividends already paid. The columns and
    # indexes are harmless, so only the empty case is reversible.
    bind = op.get_bind()
    for table in (
        "recurring_payments",
        "x402_invoices",
        "creator_dividends",
        "creator_token_holders",
        "creator_tokens",
        "notification_outbox",
        "login_nonces",
        "paymaster_usage",
    ):
        if bind.execute(f"SELECT 1 FROM {table} LIMIT 1").scalar() is not None:
            raise RuntimeError(f"Cannot downgrade while {table} holds rows")
    for table in ("creator_dividends", "creator_token_holders", "creator_tokens", "x402_invoices",
                  "recurring_payments", "login_nonces", "paymaster_usage", "gas_drips",
                  "market_subsidies", "notification_outbox"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
