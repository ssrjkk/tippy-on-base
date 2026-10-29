"""Ledger domain mixin: LedgerViewsMixin (split from bot/ledger.py)."""
import json
import time
from datetime import UTC, datetime

from ..fees import win_fee_micro
from ._base import MICRO, MIRRORED_CREDIT_KINDS


class LedgerViewsMixin:
    def top_tippers(self, limit: int = 10, since_days: int | None = None) -> list[dict]:
        since = None
        if since_days:
            since = int(datetime.now(UTC).timestamp()) - since_days * 86400
        with self._lock:
            if since:
                rows = self._conn.execute(
                    "SELECT tg_id, SUM(amount) AS total FROM tx_log "
                    "WHERE kind = 'tip' AND created_at >= %s "
                    "GROUP BY tg_id ORDER BY total DESC LIMIT %s",
                    (since, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT tg_id, SUM(amount) AS total FROM tx_log "
                    "WHERE kind = 'tip' GROUP BY tg_id ORDER BY total DESC LIMIT %s",
                    (limit,),
                ).fetchall()
        return rows



    def user_bet_stake(self, bet_id: int, tg_id: int) -> dict[int, int]:
        """option_idx -> total stake of this user in a market."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT option_idx, SUM(amount_micro) AS amount FROM bet_positions "
                "WHERE bet_id = %s AND tg_id = %s GROUP BY option_idx",
                (bet_id, tg_id),
            ).fetchall()
        return {int(r["option_idx"]): int(r["amount"]) for r in rows}



    def user_positions(self, tg_id: int) -> list[dict]:
        """Open bets where the user has a position: stake, current pool, potential payout."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT bet_id, option_idx, SUM(amount_micro) AS amount "
                "FROM bet_positions WHERE tg_id = %s "
                "GROUP BY bet_id, option_idx",
                (tg_id,),
            ).fetchall()
        if not rows:
            return []
        bet_ids = list({int(r["bet_id"]) for r in rows})
        placeholders = ",".join(["%s"] * len(bet_ids))
        with self._lock:
            bets = self._conn.execute(
                f"SELECT * FROM bets WHERE id IN ({placeholders})", bet_ids
            ).fetchall()
            totals_rows = self._conn.execute(
                f"SELECT bet_id, option_idx, SUM(amount_micro) AS total "
                f"FROM bet_positions WHERE bet_id IN ({placeholders}) "
                f"GROUP BY bet_id, option_idx",
                bet_ids,
            ).fetchall()
        bets_map = {int(b["id"]): b for b in bets}
        totals_map: dict[int, dict[int, int]] = {}
        for r in totals_rows:
            bid = int(r["bet_id"])
            totals_map.setdefault(bid, {})[int(r["option_idx"])] = int(r["total"])
        out = []
        for r in rows:
            bet_id = int(r["bet_id"])
            bet = bets_map.get(bet_id)
            if not bet or bet["status"] != "open":
                continue
            options = json.loads(bet["options"])
            totals = totals_map.get(bet_id, {})
            pot = sum(totals.values())
            opt_idx = int(r["option_idx"])
            stake = int(r["amount"])
            win_stake = totals.get(opt_idx, 0)
            gross = stake * pot // win_stake if win_stake else 0
            out.append(
                {
                    "bet_id": bet_id,
                    "question": bet["question"],
                    "option": options[opt_idx],
                    "option_idx": opt_idx,
                    "stake_micro": stake,
                    "potential_micro": gross,
                    # What resolve_bet will actually credit, from the same
                    # formula — a preview that rounds differently is a promise
                    # the ledger will break.
                    "net_micro": gross - win_fee_micro(gross - stake),
                    "close_at": bet["close_at"],
                }
            )
        return out



    def user_stats(self, tg_id: int) -> tuple[int, int, int, int]:
        """(tips_sent, tips_received, bets_won, bets_lost) in micro-units."""
        with self._lock:
            sent = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE tg_id = %s AND kind = 'tip'",
                (tg_id,),
            ).fetchone()["s"]
            # Incoming money has its own row per recipient — `tip_in` for a
            # direct or reaction tip, `rain_in` for a share of a rain. Reading
            # the sender's `tip` row instead (counterparty = me) left the
            # receiver's /history empty while /stats reported the income, and
            # made a reaction tip, whose row was filed under the receiver, read
            # as if they had tipped it away.
            received = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log "
                "WHERE tg_id = %s AND kind IN ('tip_in', 'rain_in', 'x402')",
                (tg_id,),
            ).fetchone()["s"]
            won = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE tg_id = %s AND kind = 'bet_win'",
                (tg_id,),
            ).fetchone()["s"]
            lost = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log WHERE tg_id = %s AND kind = 'bet'",
                (tg_id,),
            ).fetchone()["s"]
        return int(sent), int(received), int(won), int(lost)



    def recent_tip_targets(self, tg_id: int, limit: int = 6) -> list[int]:
        """The users this sender tipped most recently, newest first.

        The tip screen offers these as buttons: tipping the same person again is
        the common case, and recalling an @username from memory is not. Numeric
        counterparties only — a tip filed against a basename or an x402 payer is
        not a tg_id. Self-tips exist in the journal (nothing forbids them), but a
        «tip yourself» chip is a suggestion no user asked for, so own id is out.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT counterparty FROM tx_log"
                " WHERE tg_id = %s AND kind = 'tip' AND counterparty ~ '^[0-9]+$'"
                "   AND counterparty <> %s"
                # created_at is a whole second, so two tips in the same second
                # would order arbitrarily; the row id breaks the tie by insertion.
                " GROUP BY counterparty"
                " ORDER BY MAX(created_at) DESC, MAX(id) DESC LIMIT %s",
                (tg_id, str(tg_id), limit),
            ).fetchall()
        return [int(r["counterparty"]) for r in rows]



    def creator_fees(self, tg_id: int) -> int:
        """Total 2% win-fee income this user earned as a market creator."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM tx_log "
                "WHERE tg_id = %s AND kind = 'creator_income'",
                (tg_id,),
            ).fetchone()
        return int(row["s"])



    def market_view(self, bet_id: int) -> dict | None:
        """Public view of a market: options, pools, implied probability, status."""
        bet = self.get_bet(bet_id)
        if not bet:
            return None
        options = json.loads(bet["options"])
        totals = self.bet_totals(bet_id)
        pot = sum(totals.values())
        backers = self._backers_per_option(bet_id)
        items = []
        for i, opt in enumerate(options):
            pool = totals.get(i, 0)
            prob = round(pool / pot * 100, 1) if pot else 0.0
            items.append(
                {
                    "index": i,
                    "label": opt,
                    "pool": pool,
                    "probability": prob,
                    "backers": backers.get(i, 0),
                }
            )
        creator_name = self.username_of(bet["creator"])
        return {
            "id": bet["id"],
            "question": bet["question"],
            "status": bet["status"],
            "winner": bet["winner"],
            "creator": {"id": bet["creator"], "username": creator_name},
            "options": items,
            "pot": pot,
            "total_backers": sum(backers.values()),
            "close_at": bet["close_at"],
            "expired": bet["status"] == "open" and self.is_expired(bet),
            "created_at": bet["created_at"],
        }



    def _backers_per_option(self, bet_id: int) -> dict[int, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT option_idx, COUNT(DISTINCT tg_id) AS c FROM bet_positions "
                "WHERE bet_id = %s GROUP BY option_idx",
                (bet_id,),
            ).fetchall()
        return {int(r["option_idx"]): int(r["c"]) for r in rows}



    def bulk_market_views(self, bet_ids: list[int]) -> list[dict]:
        """Batch market_view for multiple bet IDs — fixes N+1 query pattern.

        Results come back in bet_ids order, not the database's: callers take the
        ids from bets_by_status (ORDER BY id DESC) and /api/markets shows that
        order, while `WHERE id IN (...)` returns rows in whatever order the
        planner picks. And every key market_view has is reproduced — `expired`
        especially, because app.js renders "истёк — можно вернуть деньги" from it,
        so the list used to contradict the detail page for the same bet. The
        creator's username comes from a join, not a per-row username_of().
        """
        if not bet_ids:
            return []
        ids = list(dict.fromkeys(int(i) for i in bet_ids))
        placeholders = ",".join(["%s"] * len(ids))
        with self._lock:
            bets = self._conn.execute(
                f"SELECT b.*, u.username AS creator_username FROM bets b "
                f"LEFT JOIN users u ON u.tg_id = b.creator "
                f"WHERE b.id IN ({placeholders})",
                ids,
            ).fetchall()
            totals_rows = self._conn.execute(
                f"SELECT bet_id, option_idx, SUM(amount_micro) AS total "
                f"FROM bet_positions WHERE bet_id IN ({placeholders}) "
                f"GROUP BY bet_id, option_idx", ids
            ).fetchall()
            backers_rows = self._conn.execute(
                f"SELECT bet_id, option_idx, COUNT(DISTINCT tg_id) AS c "
                f"FROM bet_positions WHERE bet_id IN ({placeholders}) "
                f"GROUP BY bet_id, option_idx", ids
            ).fetchall()
        totals_map: dict[int, dict[int, int]] = {}
        for r in totals_rows:
            bid = int(r["bet_id"])
            totals_map.setdefault(bid, {})[int(r["option_idx"])] = int(r["total"])
        backers_map: dict[int, dict[int, int]] = {}
        for r in backers_rows:
            bid = int(r["bet_id"])
            backers_map.setdefault(bid, {})[int(r["option_idx"])] = int(r["c"])
        bets_map = {int(b["id"]): b for b in bets}
        out = []
        for bid in ids:
            bet = bets_map.get(bid)
            if not bet:
                continue
            options = json.loads(bet["options"])
            totals = totals_map.get(bid, {})
            pot = sum(totals.values())
            backers = backers_map.get(bid, {})
            items = []
            for i, opt in enumerate(options):
                pool = totals.get(i, 0)
                prob = round(pool / pot * 100, 1) if pot else 0.0
                items.append({"index": i, "label": opt, "pool": pool, "probability": prob, "backers": backers.get(i, 0)})
            out.append({
                "id": bid, "question": bet["question"], "status": bet["status"],
                "winner": bet["winner"], "close_at": bet["close_at"],
                "creator": {"id": bet["creator"], "username": bet["creator_username"]},
                "options": items, "pot": pot,
                "total_backers": sum(backers.values()),
                "expired": bet["status"] == "open" and self.is_expired(bet),
                "created_at": bet["created_at"],
            })
        return out



    def payouts_for(self, bet_id: int) -> list[dict]:
        """Per-backer outcome of a RESOLVED market (deterministic re-computation
        of the parimutuel math used by resolve_bet). Used for result DMs."""
        with self._lock:
            bet = self.get_bet(bet_id)
            if not bet or bet["status"] != "resolved" or bet["winner"] is None:
                return []
            options = json.loads(bet["options"])
            positions = self._bet_positions(bet_id)
        total_pot = sum(int(p["amount"]) for p in positions)
        winners = [p for p in positions if int(p["option_idx"]) == int(bet["winner"])]
        win_stake = sum(int(p["amount"]) for p in winners)
        out = []
        for p in positions:
            amt = int(p["amount"])
            is_win = int(p["option_idx"]) == int(bet["winner"])
            net = 0
            if is_win:
                gross = amt * total_pot // win_stake
                fee = win_fee_micro(gross - amt)
                net = gross - fee
            out.append(
                {
                    "tg_id": int(p["tg_id"]),
                    "win": is_win,
                    "option": options[int(p["option_idx"])],
                    "amount_micro": amt,
                    "net_micro": net,
                }
            )
        return out



    def volume_history(self, days: int = 14) -> list[dict]:
        """Daily processed volume (tips + deposits + bets) for the last N days."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT to_timestamp(created_at)::date AS day, "
                "COALESCE(SUM(CASE WHEN kind IN ('tip', 'deposit', 'bet', 'x402') THEN amount END), 0) AS v "
                "FROM tx_log WHERE created_at >= %s GROUP BY day ORDER BY day",
                (int(time.time()) - days * 86400,),
            ).fetchall()
        return [{"day": r["day"], "volume_micro": int(r["v"])} for r in rows]



    def global_stats(self) -> dict:
        """Site-wide counters in one pass over the journal.

        This used to be nine queries — five of them `SUM(amount) FROM tx_log
        WHERE kind = …` — each scanning the same growing table while holding the
        ledger lock, and the landing page and /metrics asked for it constantly.
        One SELECT with a FILTER clause per kind reads tx_log once and answers
        every counter from that pass.
        """
        since30 = int(time.time()) - 30 * 86400
        with self._lock:
            row = self._conn.execute(
                "SELECT (SELECT COUNT(*) FROM users) AS users,"
                " (SELECT COUNT(*) FROM bets WHERE status = 'open') AS markets,"
                " COALESCE(SUM(amount) FILTER (WHERE kind = 'tip'), 0) AS tips,"
                " COALESCE(SUM(amount) FILTER (WHERE kind = 'deposit'), 0) AS deposits,"
                " COALESCE(SUM(amount) FILTER (WHERE kind = 'bet'), 0) AS bets_volume,"
                " COALESCE(SUM(amount) FILTER (WHERE kind = 'fee'), 0) AS fees,"
                " COALESCE(SUM(amount) FILTER (WHERE kind = 'x402'), 0) AS x402,"
                " COALESCE(SUM(amount) FILTER (WHERE kind IN ('tip', 'deposit', 'bet', 'x402')"
                "                             AND created_at >= %s), 0) AS vol30,"
                # The landing page labels this "Транзакций", so it counts
                # movements, not journal lines: the receiver-side copy of a tip
                # or a rain shares the sender's booking.
                " COUNT(*) FILTER (WHERE NOT (kind = ANY(%s))) AS txs"
                " FROM tx_log",
                (since30, list(MIRRORED_CREDIT_KINDS)),
            ).fetchone()
        return {
            "users": int(row["users"]),
            "open_markets": int(row["markets"]),
            "tips_micro": int(row["tips"]),
            "deposits_micro": int(row["deposits"]),
            "bets_micro": int(row["bets_volume"]),
            "x402_micro": int(row["x402"]),
            "fees_micro": int(row["fees"]),
            "volume_micro": int(row["tips"] + row["deposits"] + row["bets_volume"] + row["x402"]),
            "volume_30d_micro": int(row["vol30"]),
            "transactions": int(row["txs"]),
        }



    def leaderboard(self, limit: int = 10) -> list[dict]:
        rows = self.top_tippers(limit)
        names = self.usernames_bulk([r["tg_id"] for r in rows])
        return [
            {
                # tg_id travels along: a caller that has to fall back to a
                # Basename needs it, and without it the fallback never ran.
                # `username` is the Telegram username or None — a page that
                # shows the row must resolve the name, not assume one exists.
                "tg_id": int(r["tg_id"]),
                "username": names[int(r["tg_id"])],
                "total_micro": int(r["total"]),
            }
            for r in rows
        ]



    def top_balances(self, limit: int = 5) -> list[dict]:
        """The largest liabilities, by stored balance. Not the leaderboard:
        that one ranks tips sent, and an operator sizing the treasury needs to
        see whom the ledger owes the most, whether or not they ever tipped."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT tg_id, balance FROM users ORDER BY balance DESC, tg_id LIMIT %s",
                (limit,),
            ).fetchall()
        names = self.usernames_bulk([int(r["tg_id"]) for r in rows])
        return [
            {
                "tg_id": int(r["tg_id"]),
                "username": names[int(r["tg_id"])],
                "balance_micro": int(r["balance"]),
            }
            for r in rows
        ]



    def user_view(self, tg_id: int) -> dict | None:
        self.ensure_user(tg_id, None)
        sent, received, won, lost = self.user_stats(tg_id)
        return {
            "id": tg_id,
            "username": self.username_of(tg_id),
            "balance_micro": int(self.balance(tg_id) * MICRO),
            "tips_sent_micro": sent,
            "tips_received_micro": received,
            "bets_won_micro": won,
            "bets_placed_micro": lost,
            "creator_fees_micro": self.creator_fees(tg_id),
        }
