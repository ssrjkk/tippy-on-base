"""Ledger domain mixin: LedgerNotifyMixin (split from bot/ledger.py)."""



class LedgerNotifyMixin:
    def enqueue_notification(self, chat_id: int, text: str) -> int:
        """Queue a Telegram notification for retry-safe delivery."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO notification_outbox (chat_id, text) VALUES (%s, %s) RETURNING id",
                (chat_id, text),
            )
            self._conn.commit()
            return int(cur.fetchone()["id"])



    def dequeue_notifications(self, limit: int = 10) -> list[dict]:
        """Fetch pending notifications due for delivery."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, chat_id, text FROM notification_outbox "
                "WHERE next_retry_at <= EXTRACT(EPOCH FROM now())::bigint "
                "ORDER BY id LIMIT %s", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]



    def ack_notification(self, notif_id: int) -> None:
        """Mark a notification as delivered (delete it)."""
        with self._lock:
            self._conn.execute("DELETE FROM notification_outbox WHERE id = %s", (notif_id,))
            self._conn.commit()



    def retry_notification(self, notif_id: int, backoff: int = 30,
                           max_retries: int = 10) -> None:
        """Reschedule a failed notification with exponential backoff.

        The delay doubles with every recorded attempt and is capped at an hour.
        A row that fails `max_retries` times is dropped: Telegram answers
        "bot was blocked" / "chat not found" permanently, and this queue is
        drained oldest-first — a few undeliverable rows would otherwise be
        retried forever and starve every deliverable notification behind them.
        """
        import time as _time
        with self._lock:
            row = self._conn.execute(
                "UPDATE notification_outbox SET retries = retries + 1, "
                "next_retry_at = CAST(%s + LEAST(%s * POWER(2, retries), 3600) AS BIGINT) "
                "WHERE id = %s RETURNING retries",
                (int(_time.time()), int(backoff), notif_id),
            ).fetchone()
            if row is None or int(row["retries"]) >= max_retries:
                self._conn.execute("DELETE FROM notification_outbox WHERE id = %s", (notif_id,))
            self._conn.commit()



    def get_smart_wallet(self, tg_id: int) -> dict | None:
        """Get the SmartAccount info for a user, or None."""
        with self._lock:
            return self._conn.execute(
                "SELECT tg_id, smart_address, smart_deployed, smart_created_at "
                "FROM users WHERE tg_id = %s AND smart_address IS NOT NULL",
                (tg_id,),
            ).fetchone()



    def set_smart_wallet(self, tg_id: int, address: str) -> None:
        """Record the SmartAccount address for a user."""
        with self._lock:
            import time as _time
            self._conn.execute(
                "UPDATE users SET smart_address = %s, smart_deployed = true, "
                "smart_created_at = %s WHERE tg_id = %s",
                (address, int(_time.time()), tg_id),
            )
            self._conn.commit()



    def mark_smart_wallet_deployed(self, tg_id: int) -> None:
        """Mark the SmartAccount as deployed on-chain."""
        with self._lock:
            self._conn.execute(
                "UPDATE users SET smart_deployed = true WHERE tg_id = %s",
                (tg_id,),
            )
            self._conn.commit()



    def has_smart_wallet(self, tg_id: int) -> bool:
        """Check if user has a SmartAccount address recorded."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE tg_id = %s AND smart_address IS NOT NULL",
                (tg_id,),
            ).fetchone()
            return row is not None
