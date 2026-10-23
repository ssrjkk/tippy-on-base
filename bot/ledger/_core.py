"""Ledger domain mixin: LedgerCoreMixin (split from bot/ledger.py)."""
import logging
import threading
import time

import psycopg
import psycopg.errors

from .. import config
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

    def reserve_idempotency(self, key_hash: str, user_id: int, action: str) -> dict | None:
        """Reserve an idempotency key for a money POST.

        Returns None when this caller won the reservation. Returns the existing
        row ({'status', 'result_id', 'response'}) when the key was already taken,
        so the caller can hand back the first attempt's outcome instead of a
        permanent 409.

        A reservation older than `expires_at` is reclaimed first: an attempt
        that died between reserve and commit must not lock the key for its whole
        TTL. The same sweep bounds the table, which otherwise only ever grew —
        the DELETE is what the expires_at index exists for.
        """
        with self._lock:
            try:
                now = int(time.time())
                self._conn.execute("DELETE FROM idempotency_keys WHERE expires_at < %s", (now,))
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
