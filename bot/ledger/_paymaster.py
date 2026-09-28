"""Ledger domain mixin: LedgerPaymasterMixin — gasless usage persistence.

Previously the paymaster tracked free-tx counts in an in-memory dict that
reset on every restart, giving users unlimited gasless transactions. This
mixin persists the counter in PostgreSQL so the FREE_TRANSACTIONS_COUNT
allowance is enforced across process lifetimes.
"""
import time


class LedgerPaymasterMixin:
    def paymaster_get_usage(self, address: str) -> dict:
        """Return usage row for address (zeros if never seen)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT used_count, last_used FROM paymaster_usage WHERE address = %s",
                (address.lower(),),
            )
            row = cur.fetchone()
        if row:
            return {"used_count": row["used_count"], "last_used": row["last_used"]}
        return {"used_count": 0, "last_used": 0}

    def paymaster_increment(self, address: str) -> int:
        """Bump usage counter and return the new total."""
        addr = address.lower()
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO paymaster_usage (address, used_count, last_used)
                VALUES (%(addr)s, 1, %(now)s)
                ON CONFLICT (address) DO UPDATE
                    SET used_count = paymaster_usage.used_count + 1,
                        last_used = %(now)s
                RETURNING used_count
                """,
                {"addr": addr, "now": now},
            )
            row = cur.fetchone()
            self._conn.commit()
        return row["used_count"] if row else 0
