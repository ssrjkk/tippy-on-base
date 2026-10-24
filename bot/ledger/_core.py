"""Ledger domain mixin: LedgerCoreMixin (split from bot/ledger.py)."""
import logging
import threading
import time

import psycopg
import psycopg.errors

from .. import config
from ._base import IdempotencyDuplicate
from ._conn import ReconnectingConn
from ._schema import SCHEMA_DDL

# Alembic installs the names an env.py script reads (`context`, `config`, ...)
# into process-global module dicts when an EnvironmentContext is entered and
# deletes them on exit. Two threads calling `command.upgrade` inside one
# process nest those installs, so the second exit raises `KeyError: 'config'`
# for names that are already gone. _run_alembic() logs and swallows it, which
# leaves the tracked migrations unapplied while ensure_schema() papers over it.
# The advisory lock in env.py only serialises across processes, hence this one.
_ALEMBIC_LOCK = threading.Lock()


class LedgerCoreMixin:
    def __init__(self, database: str = config.DATABASE_URL) -> None:
        # One connection per Ledger instance, serialized by a per-process RLock.
        # In production the bot and the web dashboard run in SEPARATE processes
        # (see docker-compose.yml); this RLock does NOT coordinate across them.
        # All cross-process safety comes from atomic SQL (transactions, unique
        # constraints, row-level locking), not from this lock.
        self._lock = threading.RLock()
        self._conn = ReconnectingConn(database)
        self._run_alembic(database)
        self.ensure_schema()  # idempotent; retries past lock contention



    def batch_lock(self):
        """Return the ledger lock for batch operations."""
        return self._lock

    def _ensure(self) -> None:
        """Reconnect if the underlying connection is dead/broken."""
        self._conn._ensure()



    def ping(self) -> None:
        """Lightweight DB liveness check (SELECT 1). Raises on failure."""
        with self._lock:
            self._conn.execute("SELECT 1")



    @staticmethod
    def _run_alembic(database: str) -> None:
        """Run ``alembic upgrade head`` to apply tracked schema migrations.

        Best-effort: if alembic is not installed or alembic.ini / versions/
        is missing (tests, clean installs), we fall back to ensure_schema()
        which applies the full DDL idempotently. But a REAL migration failure
        (e.g. a conflicting or partially-applied migration) must not be
        silent: it is logged loudly so an operator knows the tracked schema
        (alembic/versions/*) diverged from the live DDL before money flows.

        `database` travels in Config.attributes rather than sqlalchemy.url
        because env.py rewrites sqlalchemy.url from $DATABASE_URL; without the
        marker every programmatic caller migrated the environment's database,
        which made the test suite ALTER the developer's live ledger.
        """
        try:
            import pathlib

            from alembic.config import Config

            from alembic import command
            ini = pathlib.Path(__file__).resolve().parent.parent.parent / "alembic.ini"
            if not ini.exists():
                return
            cfg = Config(str(ini), attributes={"tippy.database_url": database})
            with _ALEMBIC_LOCK:
                command.upgrade(cfg, "head")
        except Exception as e:  # ensure_schema() remains the safety net
            logging.getLogger("tipbot.alembic").exception(
                "alembic upgrade head failed: %s. ensure_schema() will apply the "
                "idempotent DDL, but the tracked migration state may be stale — "
                "check alembic_version vs the migration chain.", e
            )



    def ensure_schema(self, retries: int = 8, delay: float = 2.0) -> None:
        """Apply idempotent schema DDL, retrying past transient lock timeouts.

        Running this at Ledger() construction used to crash the whole process
        when a concurrent bot/web/test process held a lock (ALTER TABLE needs
        ACCESS EXCLUSIVE). Now we back off and retry instead of dying.
        """
        last = None
        for _ in range(retries):
            try:
                with self._lock:
                    self._conn.rollback()
                    self._conn.execute("SET lock_timeout = '30s'")
                    self._conn.execute("SET statement_timeout = '60s'")
                    self._conn.execute(SCHEMA_DDL)
                    self._conn.commit()
                return
            except (psycopg.errors.LockNotAvailable, psycopg.OperationalError) as e:
                last = e
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                time.sleep(delay)
        raise RuntimeError(f"schema migration failed after {retries} attempts: {last}")

    def reserve_idempotency_in_tx(self, key_hash: str, user_id: int, action: str) -> None:
        """Insert the reservation INTO THE CALLER'S TRANSACTION — no commit.

        This is what makes the key atomic with the money: the row and the
        balance change land together or not at all, so a `pending` row can only
        mean "a transaction is open right now". Two concurrent duplicates
        serialize on the primary key — the second waits for the first to commit
        or roll back, then gets UniqueViolation, and its own transaction aborts
        without touching a balance.

        Replaces the old reserve-then-commit-then-spend flow, which left a
        window where the money had moved and the key still said `pending`: a
        crash in that window plus the expiry sweep let the same key spend twice.
        """
        try:
            self._conn.execute(
                "INSERT INTO idempotency_keys (key_hash, user_id, action, status) "
                "VALUES (%s, %s, %s, 'pending')",
                (key_hash, user_id, action),
            )
        except psycopg.errors.UniqueViolation:
            self._conn.rollback()
            row = self._conn.execute(
                "SELECT status, result_id, response FROM idempotency_keys WHERE key_hash = %s",
                (key_hash,),
            ).fetchone()
            raise IdempotencyDuplicate(action, key_hash, dict(row) if row else None) from None

    def reserve_idempotency(self, key_hash: str, user_id: int, action: str) -> dict | None:
        """Pre-commit a reservation for an IRREVERSIBLE action (on-chain buy).

        Only for work whose effect cannot be rolled back with the transaction:
        the key has to be durable before the broadcast, so a crash mid-broadcast
        cannot be answered by a second broadcast. Returns None when this caller
        won the reservation, or the existing row when the key was taken.

        `pending` rows are never deleted here. An abandoned row must be
        reconciled by a person or a watcher, not silently forgotten so the key
        becomes spendable again.
        """
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO idempotency_keys (key_hash, user_id, action, status) "
                    "VALUES (%s, %s, %s, 'pending') ON CONFLICT (key_hash) DO NOTHING "
                    "RETURNING status",
                    (key_hash, user_id, action),
                )
                if cur.fetchone() is not None:
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    "SELECT status, result_id, response FROM idempotency_keys WHERE key_hash = %s",
                    (key_hash,),
                ).fetchone()
                self._conn.commit()
                return dict(row) if row else None
            except Exception:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                raise

    def prune_idempotency(self, now: int | None = None) -> int:
        """Drop COMPLETED reservations past their TTL — the replay cache only.

        Called from the housekeeping watcher rather than from the money path:
        `pending` rows are never touched, because for a pre-committed
        irreversible action a pending row is an alarm, not garbage.
        """
        cutoff = int(now if now is not None else time.time())
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM idempotency_keys "
                "WHERE status = 'completed' AND expires_at < %s",
                (cutoff,),
            )
            self._conn.commit()
            return cur.rowcount

    def mark_idempotency_applied(self, key_hash: str) -> None:
        """Flip the reservation to 'applied' INSIDE the money transaction.

        The three states are what makes an honest answer possible: `pending`
        can only mean a transaction is open right now (it disappears on rollback,
        so a crashed attempt leaves nothing behind), `applied` means the balances
        moved, and `completed` additionally has the response body to replay.
        Without 'applied' a duplicate of a committed-but-unstored attempt could
        not be told apart from one still in flight.
        """
        self._conn.execute(
            "UPDATE idempotency_keys SET status = 'applied' WHERE key_hash = %s",
            (key_hash,),
        )

    def complete_idempotency(
        self, key_hash: str, result_id: str | None = None, response: str | None = None
    ) -> None:
        """Mark the reservation used and store what to answer a retry with.

        `response` is the JSON body the first attempt returned; `result_id` is
        the on-chain/ledger identifier support asks for.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE idempotency_keys SET status = 'completed', result_id = %s, "
                "response = %s WHERE key_hash = %s",
                (result_id, response, key_hash),
            )
            self._conn.commit()

    def rollback_idempotency(self, key_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM idempotency_keys WHERE key_hash = %s",
                (key_hash,),
            )
            self._conn.commit()
