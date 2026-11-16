"""Ledger domain mixin: LedgerCoreMixin (split from bot/ledger.py)."""
import logging
import threading
import time

import psycopg
import psycopg.errors

from .. import config
from ._base import IdempotencyDuplicate
from ._conn import ReconnectingConn

# Alembic installs the names an env.py script reads (`context`, `config`, ...)
# into process-global module dicts when an EnvironmentContext is entered and
# deletes them on exit. Two threads calling `command.upgrade` inside one
# process nest those installs, so the second exit raises `KeyError: 'config'`
# for names that are already gone. _run_alembic() logs and swallows it, which
# leaves the tracked migrations unapplied, and the process would then run on a
# schema it never asked for. The advisory lock in env.py only serialises across
# processes, hence this one.
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
        # alembic/versions/* is the only writer of this database's schema. There is
        # deliberately no second DDL applied after it: the two used to disagree in
        # both directions, and applying the string on top of the chain hid the gap
        # until 018 closed it. See _run_alembic().
        self._run_alembic(database)



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



    # Postgres states that mean "someone else holds the schema lock right now" or
    # "the server was not reachable this instant" — both are worth another attempt.
    _TRANSIENT_PGCODES = frozenset({"55P03", "40P01", "08000", "08001", "08003", "57P01"})
    _TRANSIENT_NAMES = frozenset({"LockNotAvailable", "DeadlockDetected", "OperationalError", "InterfaceError"})

    @classmethod
    def _is_transient(cls, exc: BaseException) -> bool:
        """Walk the exception chain: SQLAlchemy wraps psycopg, psycopg wraps the
        Postgres state, and the retry decision belongs to the innermost one."""
        seen: set[int] = set()
        node: BaseException | None = exc
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            for candidate in (node, getattr(node, "orig", None)):
                if candidate is not None and type(candidate).__name__ in cls._TRANSIENT_NAMES:
                    return True
            state = getattr(node, "sqlstate", None) or getattr(node, "pgcode", None)
            if state in cls._TRANSIENT_PGCODES:
                return True
            node = node.__cause__ or node.__context__
        return False

    @staticmethod
    def _run_alembic(database: str, retries: int = 8, delay: float = 2.0) -> None:
        """Apply the tracked chain — the only writer of this database's schema.

        Used to be best-effort: any failure was logged and swallowed because
        `ensure_schema()` applied an idempotent DDL string afterwards. That made
        two schema sources legal at the same time, and they drifted in both
        directions: the chain lacked `recurring_payments`, `x402_invoices`,
        `login_nonces`, `paymaster_usage` and `users.smart_address`, while the
        string lacked `idempotency_keys` and `audit_logs`. A process whose
        migration raised therefore started serving money on a database missing the
        table its own reservation writes into — with only a log line to say so.

        Now: lock contention and an unreachable server are retried (several
        processes call this at once on boot, and ALTER needs ACCESS EXCLUSIVE);
        everything else propagates. A database whose schema is unknown is not a
        database to move USDC in.

        `database` travels in Config.attributes rather than sqlalchemy.url
        because env.py rewrites sqlalchemy.url from $DATABASE_URL; without the
        marker every programmatic caller migrated the environment's database,
        which made the test suite ALTER the developer's live ledger.
        """
        import pathlib

        from alembic.config import Config

        from alembic import command

        log = logging.getLogger("tipbot.alembic")
        ini = pathlib.Path(__file__).resolve().parent.parent.parent / "alembic.ini"
        if not ini.exists():
            raise RuntimeError(
                "alembic.ini is missing — alembic/versions/* is the only schema source, "
                "so the process cannot start without the chain it is supposed to apply."
            )
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                cfg = Config(str(ini), attributes={"tippy.database_url": database})
                with _ALEMBIC_LOCK:
                    command.upgrade(cfg, "head")
                return
            except Exception as e:
                if not LedgerCoreMixin._is_transient(e):
                    log.exception("alembic upgrade head failed: %s", e)
                    raise
                last = e
                log.warning(
                    "alembic upgrade head contended (attempt %d/%d): %s", attempt, retries, e
                )
                time.sleep(delay)
        raise RuntimeError(
            f"the schema stayed locked through {retries} alembic attempts: {last}"
        )

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
