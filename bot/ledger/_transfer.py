"""Ledger domain mixin: LedgerTransferMixin (split from bot/ledger.py)."""
import json

from ._base import audit_log


class LedgerTransferMixin:
    def transfer(
        self, from_id: int, to_id: int, amount_micro: int, note: str = "",
        idem: tuple[str, int] | None = None,
    ) -> bool:
        """Move funds between users. Returns False if sender lacks balance.

        One row per side. A single `tip` row (tg_id = payer) made the payment
        invisible in the receiver's /history while /stats counted it as their
        income, and recurring payments (bot/main.py) have a memo to record.

        `idem=(key_hash, user_id)` reserves the caller's Idempotency-Key inside
        THIS transaction, so the reservation and both balance updates land or
        roll back together: a duplicate can never spend twice, and a key can
        never read `pending` after the money already moved.
        """
        if amount_micro <= 0:
            # A negative amount would invert the direction (the "sender"
            # would GAIN money) and mint it from thin air.
            return False
        with self._lock:
            committed = False
            try:
                if idem is not None:
                    self.reserve_idempotency_in_tx(idem[0], idem[1], "tip")
                self.ensure_user(to_id, None, commit=False)
                cur = self._conn.execute(
                    "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                    (amount_micro, from_id, amount_micro),
                )
                if cur.rowcount == 0:
                    self._conn.rollback()
                    return False
                self._conn.execute(
                    "UPDATE users SET balance = balance + %s WHERE tg_id = %s",
                    (amount_micro, to_id),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                    "VALUES ('tip', %s, %s, %s, %s)",
                    (from_id, str(to_id), amount_micro, note),
                )
                self._conn.execute(
                    "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) "
                    "VALUES ('tip_in', %s, %s, %s, %s)",
                    (to_id, str(from_id), amount_micro, note),
                )
                if idem is not None:
                    self.mark_idempotency_applied(idem[0])
                self._conn.commit()
                committed = True
                audit_log.info(json.dumps({"event": "transfer", "from": from_id, "to": to_id, "amount_micro": amount_micro}))
                return True
            finally:
                if not committed:
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass



    def debit(self, tg_id: int, amount_micro: int) -> bool:
        # NOTE: unlike transfer(), debit() leaves its transaction open on the
        # SUCCESS path (the caller must commit — most buy/tip/bet flows batch
        # several writes into one transaction). On the FAILURE path we roll
        # back here so the shared connection never carries a stale write into
        # the next unrelated ledger call; callers that roll back after a
        # failed debit are unaffected (rollback is idempotent).
        if amount_micro <= 0:
            raise ValueError(f"debit amount must be positive (got {amount_micro})")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE users SET balance = balance - %s WHERE tg_id = %s AND balance >= %s",
                (amount_micro, tg_id, amount_micro),
            )
            if cur.rowcount > 0:
                audit_log.info(json.dumps({"event": "debit", "tg_id": tg_id, "amount_micro": amount_micro, "note": ""}))
                return True
            self._conn.rollback()
            return False
