"""Ledger domain mixin: LedgerOnchainMixin (split from bot/ledger.py)."""
import json
import time


class LedgerOnchainMixin:
    def save_onchain_market(
        self, market_id: int, creator: int, question: str, options: list[str], close_at: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO onchain_markets (id, creator, question, options, close_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (market_id, creator, question, json.dumps(options), close_at),
            )
            self._conn.commit()



    def get_onchain_market(self, market_id: int) -> dict | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM onchain_markets WHERE id = %s", (market_id,)
            ).fetchone()



    def list_onchain_markets(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM onchain_markets ORDER BY id DESC LIMIT %s", (limit,)
            ).fetchall()



    def onchain_markets_past_deadline(self) -> list[dict]:
        """Registered on-chain markets past close the creator wasn't asked to
        resolve yet (the watcher DMs them outcome-pick buttons)."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, creator, question, options FROM onchain_markets "
                "WHERE resolved_outcome IS NULL AND cancelled_flag = 0 "
                "AND deadline_notified = 0 AND close_at <= %s",
                (int(time.time()),),
            ).fetchall()



    def mark_onchain_deadline_notified(self, market_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE onchain_markets SET deadline_notified = 1 WHERE id = %s",
                (market_id,),
            )
            self._conn.commit()



    def set_onchain_resolved(self, market_id: int, winner_idx: int) -> bool:
        """Record the winning outcome — only while the market is still open.

        Guarded because the watcher and /oc_resolve race: overwriting a
        cancelled flag with a winner would DM holders a redemption that the
        contract refuses with AlreadyCancelled. Returns False if the row had
        already settled.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE onchain_markets SET resolved_outcome = %s "
                "WHERE id = %s AND resolved_outcome IS NULL AND cancelled_flag = 0",
                (winner_idx, market_id),
            )
            self._conn.commit()
            return cur.rowcount > 0



    def mark_onchain_cancelled(self, market_id: int) -> bool:
        """Flag a market as cancelled — only while it is still open (see
        :meth:`set_onchain_resolved` for why the flip must be one-way)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE onchain_markets SET cancelled_flag = 1 "
                "WHERE id = %s AND resolved_outcome IS NULL AND cancelled_flag = 0",
                (market_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0



    def onchain_markets_overdue(self, grace_seconds: int) -> list[dict]:
        """Unresolved on-chain markets whose cancel window (24h on-chain) plus
        `grace_seconds` has passed — the watcher cancels them so holders can
        pull refunds and the creator subsidy is not stuck forever."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, question FROM onchain_markets "
                "WHERE resolved_outcome IS NULL AND cancelled_flag = 0 "
                "AND close_at + %s <= %s",
                (grace_seconds, int(time.time())),
            ).fetchall()



    def record_onchain_trade(
        self, market_id: int, tg_id: int, outcome: int, shares: int, tx_hash: str = ""
    ) -> None:
        """Log a fill against the registry: buys positive, sells negative, so
        SUM(shares) tracks what a user still holds. Registry-only: the ERC-1155
        balance remains the source of truth, this powers the winner/refund DMs.
        """
        with self._lock:
            self.ensure_user(tg_id, None)
            self._conn.execute(
                "INSERT INTO onchain_trades (market_id, tg_id, outcome, shares, tx_hash) "
                "VALUES (%s, %s, %s, %s, %s)",
                (market_id, tg_id, outcome, shares, tx_hash),
            )
            self._conn.commit()



    def onchain_trades_for_outcome(self, market_id: int, outcome: int) -> list[dict]:
        """Per-user net shares still held of one outcome. A holder who sold out
        nets to zero or less and drops out; the contract balance is what
        actually decides a payout."""
        with self._lock:
            return self._conn.execute(
                "SELECT tg_id, SUM(shares) AS shares FROM onchain_trades "
                "WHERE market_id = %s AND outcome = %s "
                "GROUP BY tg_id HAVING SUM(shares) > 0",
                (market_id, outcome),
            ).fetchall()



    def onchain_holders(self, market_id: int) -> list[dict]:
        """Everyone holding any outcome of a market (net of sells). Used to DM
        refund instructions when a market is cancelled."""
        with self._lock:
            return self._conn.execute(
                "SELECT tg_id, SUM(shares) AS shares FROM onchain_trades "
                "WHERE market_id = %s GROUP BY tg_id HAVING SUM(shares) > 0",
                (market_id,),
            ).fetchall()
