"""Ledger domain mixin: LedgerRecurringMixin — recurring payment persistence.

Backing store for bot.recurring.RecurringPaymentStore. All scheduling math
(interval → next_execution) stays in the store; this mixin only moves rows.
The atomic claim (mark_executed) uses a compare-and-set on next_execution
so two concurrent executor loops cannot double-debit the same payment.
"""
import time


class LedgerRecurringMixin:
    def recurring_insert(self, p: dict) -> None:
        """Insert a new recurring payment. Idempotent: ON CONFLICT DO NOTHING
        so a retry after a crash does not duplicate the row."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO recurring_payments
                    (id, from_tg_id, to_tg_id, amount_micro, interval, memo,
                     active, created_at, next_execution, last_execution,
                     execution_count, max_executions)
                VALUES (%(id)s, %(from_tg_id)s, %(to_tg_id)s, %(amount_micro)s,
                        %(interval)s, %(memo)s, %(active)s, %(created_at)s,
                        %(next_execution)s, %(last_execution)s,
                        %(execution_count)s, %(max_executions)s)
                ON CONFLICT (id) DO NOTHING
                """,
                p,
            )
            self._conn.commit()

    def recurring_cancel(self, payment_id: str, tg_id: int) -> bool:
        """Deactivate a payment. Returns True if the row existed and belonged
        to tg_id (single-statement guard so a concurrent cancel cannot
        succeed twice)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE recurring_payments SET active = false "
                "WHERE id = %s AND from_tg_id = %s AND active = true",
                (payment_id, tg_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def recurring_get_due(self, now: int) -> list[dict]:
        """Return all active payments whose next_execution <= now and whose
        max_executions has not been reached (0 = unlimited)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM recurring_payments "
                "WHERE active = true AND next_execution <= %s "
                "AND (max_executions = 0 OR execution_count < max_executions)",
                (now,),
            ).fetchall()
        return rows

    def recurring_mark_executed(self, payment_id: str, interval_seconds: int) -> bool:
        """Atomically advance the payment's schedule. The WHERE clause checks
        that the payment is still active and due; a concurrent executor that
        already claimed it will see rowcount == 0 and skip the debit.
        Returns True if the claim succeeded (caller should debit)."""
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE recurring_payments
                SET last_execution = %(now)s,
                    execution_count = execution_count + 1,
                    next_execution = %(now)s + %(interval_seconds)s,
                    active = CASE
                        WHEN max_executions > 0 AND execution_count + 1 >= max_executions
                        THEN false ELSE active END
                WHERE id = %(payment_id)s
                  AND active = true
                  AND next_execution <= %(now)s
                """,
                {"now": now, "interval_seconds": interval_seconds, "payment_id": payment_id},
            )
            self._conn.commit()
        return cur.rowcount > 0

    def recurring_list_for_user(self, tg_id: int) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM recurring_payments "
                "WHERE from_tg_id = %s OR to_tg_id = %s "
                "ORDER BY created_at DESC",
                (tg_id, tg_id),
            ).fetchall()

    def recurring_get(self, payment_id: str) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM recurring_payments WHERE id = %s",
                (payment_id,),
            ).fetchone()

    def recurring_legacy_import(self, payments: list[dict]) -> int:
        """Import legacy JSON state only if the table is empty.
        Returns the number of rows written (0 = table already had data)."""
        with self._lock:
            self._conn.execute("SELECT pg_advisory_xact_lock(823452)")
            n = self._conn.execute(
                "SELECT COUNT(*) AS n FROM recurring_payments"
            ).fetchone()["n"]
            if int(n) > 0:
                self._conn.rollback()
                return 0
            for p in payments:
                self._conn.execute(
                    """
                    INSERT INTO recurring_payments
                        (id, from_tg_id, to_tg_id, amount_micro, interval, memo,
                         active, created_at, next_execution, last_execution,
                         execution_count, max_executions)
                    VALUES (%(id)s, %(from_tg_id)s, %(to_tg_id)s, %(amount_micro)s,
                            %(interval)s, %(memo)s, %(active)s, %(created_at)s,
                            %(next_execution)s, %(last_execution)s,
                            %(execution_count)s, %(max_executions)s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    p,
                )
            self._conn.commit()
        return len(payments)
