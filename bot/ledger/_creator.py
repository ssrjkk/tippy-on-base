"""Ledger domain mixin: LedgerCreatorMixin — creator token persistence.

Backing store for bot.creator_tokens.CreatorTokenRegistry. All money math
(cost formulas, dividend-per-token) stays in the registry; this mixin only
moves rows. Distribution and claims are atomic single statements so a crash
can never leave a holder paid-but-unrecorded.
"""
import time


class LedgerCreatorMixin:
    # ---------- tokens ----------

    def creator_token_upsert(self, t: dict) -> None:
        """Insert (or fully overwrite) a token row. Used by create_token and
        by the one-time legacy JSON import (idempotent re-import safety)."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO creator_tokens
                    (token_id, creator_tg_id, name, symbol, total_supply, price_micro,
                     created_at, total_revenue_micro, total_dividends_paid_micro,
                     dividend_per_token_micro)
                VALUES (%(token_id)s, %(creator_tg_id)s, %(name)s, %(symbol)s,
                        %(total_supply)s, %(price_micro)s, %(created_at)s,
                        %(total_revenue_micro)s, %(total_dividends_paid_micro)s,
                        %(dividend_per_token_micro)s)
                ON CONFLICT (token_id) DO UPDATE SET
                    creator_tg_id = EXCLUDED.creator_tg_id,
                    name = EXCLUDED.name,
                    symbol = EXCLUDED.symbol,
                    total_supply = EXCLUDED.total_supply,
                    price_micro = EXCLUDED.price_micro,
                    created_at = EXCLUDED.created_at,
                    total_revenue_micro = EXCLUDED.total_revenue_micro,
                    total_dividends_paid_micro = EXCLUDED.total_dividends_paid_micro,
                    dividend_per_token_micro = EXCLUDED.dividend_per_token_micro
                """,
                t,
            )
            self._conn.commit()

    def creator_token_get(self, token_id: str) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM creator_tokens WHERE token_id = %s", (token_id,)
            ).fetchone()

    def creator_tokens_by_creator(self, creator_tg_id: int) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM creator_tokens WHERE creator_tg_id = %s ORDER BY created_at",
                (creator_tg_id,),
            ).fetchall()

    def creator_tokens_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM creator_tokens").fetchone()
        return int(row["n"])

    # ---------- holders ----------

    _HOLDER_COLS = "token_id, holder_tg_id, balance, pending_dividends_micro, last_dividend_claim"

    def creator_holder_add(self, token_id: str, holder_tg_id: int, amount: int) -> dict:
        """Credit `amount` tokens to a holder, creating the row if needed."""
        with self._lock:
            row = self._conn.execute(
                f"""
                INSERT INTO creator_token_holders (token_id, holder_tg_id, balance)
                VALUES (%s, %s, %s)
                ON CONFLICT (token_id, holder_tg_id)
                DO UPDATE SET balance = creator_token_holders.balance + EXCLUDED.balance
                RETURNING {self._HOLDER_COLS}
                """,
                (token_id, holder_tg_id, amount),
            ).fetchone()
            self._conn.commit()
        return row

    def creator_holder_sub(self, token_id: str, holder_tg_id: int, amount: int) -> dict | None:
        """Debit `amount` tokens; None if the holder is missing or short
        (single-statement guard, so a concurrent sale cannot go negative)."""
        with self._lock:
            row = self._conn.execute(
                f"""
                UPDATE creator_token_holders
                SET balance = balance - %s
                WHERE token_id = %s AND holder_tg_id = %s AND balance >= %s
                RETURNING {self._HOLDER_COLS}
                """,
                (amount, token_id, holder_tg_id, amount),
            ).fetchone()
            self._conn.commit()
        return row

    def creator_holder_get(self, token_id: str, holder_tg_id: int) -> dict | None:
        with self._lock:
            return self._conn.execute(
                f"SELECT {self._HOLDER_COLS} FROM creator_token_holders "
                "WHERE token_id = %s AND holder_tg_id = %s",
                (token_id, holder_tg_id),
            ).fetchone()

    def creator_holder_upsert(self, h: dict) -> None:
        """Full-row upsert — legacy import only."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO creator_token_holders
                    (token_id, holder_tg_id, balance, pending_dividends_micro,
                     last_dividend_claim)
                VALUES (%(token_id)s, %(holder_tg_id)s, %(balance)s,
                        %(pending_dividends_micro)s, %(last_dividend_claim)s)
                ON CONFLICT (token_id, holder_tg_id) DO UPDATE SET
                    balance = EXCLUDED.balance,
                    pending_dividends_micro = EXCLUDED.pending_dividends_micro,
                    last_dividend_claim = EXCLUDED.last_dividend_claim
                """,
                h,
            )
            self._conn.commit()

    def creator_holders_summary(self, token_id: str) -> tuple[int, int]:
        """(total tokens held, holder count) for a token."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(balance), 0) AS total, COUNT(*) AS n "
                "FROM creator_token_holders WHERE token_id = %s",
                (token_id,),
            ).fetchone()
        return int(row["total"]), int(row["n"])

    # ---------- dividends ----------

    def creator_dividend_apply(
        self, token_id: str, amount_micro: int, dividend_per_token: float, total_holders: int
    ) -> dict | None:
        """Atomically credit every holder's pending balance and record the
        distribution. FLOOR() matches the registry's int() truncation for the
        non-negative values this domain produces. Locks the token row so two
        processes cannot double-distribute the same revenue."""
        with self._lock:
            self._conn.execute(
                "SELECT token_id FROM creator_tokens WHERE token_id = %s FOR UPDATE",
                (token_id,),
            )
            cur = self._conn.execute(
                """
                UPDATE creator_token_holders
                SET pending_dividends_micro =
                        pending_dividends_micro + FLOOR(balance * %s)::bigint
                WHERE token_id = %s
                """,
                (dividend_per_token, token_id),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                return None
            self._conn.execute(
                """
                UPDATE creator_tokens
                SET total_revenue_micro = total_revenue_micro + %s,
                    total_dividends_paid_micro = total_dividends_paid_micro + %s,
                    dividend_per_token_micro = dividend_per_token_micro + %s
                WHERE token_id = %s
                """,
                (amount_micro, amount_micro, dividend_per_token, token_id),
            )
            self._conn.execute(
                """
                INSERT INTO creator_dividends
                    (token_id, amount_micro, dividend_per_token, total_holders)
                VALUES (%s, %s, %s, %s)
                """,
                (token_id, amount_micro, dividend_per_token, total_holders),
            )
            self._conn.commit()
        return {
            "token_id": token_id,
            "amount_micro": amount_micro,
            "dividend_per_token": dividend_per_token,
            "total_holders": total_holders,
            "timestamp": time.time(),
        }

    def creator_holder_claim(self, token_id: str, holder_tg_id: int) -> int:
        """Zero out and return a holder's pending dividends (0 if none).
        The compare-and-set WHERE clause makes a cross-process double claim
        return 0 instead of paying twice."""
        with self._lock:
            row = self._conn.execute(
                "SELECT pending_dividends_micro FROM creator_token_holders "
                "WHERE token_id = %s AND holder_tg_id = %s",
                (token_id, holder_tg_id),
            ).fetchone()
            if not row or int(row["pending_dividends_micro"]) == 0:
                self._conn.rollback()
                return 0
            pending = int(row["pending_dividends_micro"])
            cur = self._conn.execute(
                "UPDATE creator_token_holders "
                "SET pending_dividends_micro = 0, last_dividend_claim = %s "
                "WHERE token_id = %s AND holder_tg_id = %s "
                "AND pending_dividends_micro = %s",
                (time.time(), token_id, holder_tg_id, pending),
            )
            if cur.rowcount == 0:
                self._conn.rollback()
                return 0
            self._conn.commit()
        return pending

    # ---------- legacy JSON import (one-time) ----------

    def creator_legacy_import(self, tokens: list[dict], holders: dict[str, list[dict]]) -> int:
        """Import legacy JSON state only if the tables are empty.

        Runs inside one transaction guarded by a PostgreSQL advisory lock, so
        the bot and the web process (or two racing starts) cannot both import.
        Returns the number of token rows written (0 = tables already had data).
        """
        with self._lock:
            self._conn.execute("SELECT pg_advisory_xact_lock(823451)")
            n = self._conn.execute("SELECT COUNT(*) AS n FROM creator_tokens").fetchone()["n"]
            if int(n) > 0:
                self._conn.rollback()
                return 0
            for t in tokens:
                self._conn.execute(
                    """
                    INSERT INTO creator_tokens
                        (token_id, creator_tg_id, name, symbol, total_supply, price_micro,
                         created_at, total_revenue_micro, total_dividends_paid_micro,
                         dividend_per_token_micro)
                    VALUES (%(token_id)s, %(creator_tg_id)s, %(name)s, %(symbol)s,
                            %(total_supply)s, %(price_micro)s, %(created_at)s,
                            %(total_revenue_micro)s, %(total_dividends_paid_micro)s,
                            %(dividend_per_token_micro)s)
                    ON CONFLICT (token_id) DO NOTHING
                    """,
                    t,
                )
            for h in holders:
                for hd in holders[h]:
                    self._conn.execute(
                        """
                        INSERT INTO creator_token_holders
                            (token_id, holder_tg_id, balance, pending_dividends_micro,
                             last_dividend_claim)
                        VALUES (%(token_id)s, %(holder_tg_id)s, %(balance)s,
                                %(pending_dividends_micro)s, %(last_dividend_claim)s)
                        ON CONFLICT (token_id, holder_tg_id) DO NOTHING
                        """,
                        hd,
                    )
            self._conn.commit()
        return len(tokens)
