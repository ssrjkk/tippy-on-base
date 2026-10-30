"""Notification outbox: verify that background watchers enqueue notifications
instead of sending directly, so delivery survives Telegram outages."""

import pytest


@pytest.mark.asyncio
async def test_enqueue_notification_persists(ledger):
    """enqueue_notification must persist the message to the outbox table."""
    notif_id = ledger.enqueue_notification(123456, "Test message")
    assert notif_id > 0

    # The notification should be in the outbox.
    items = ledger.dequeue_notifications()
    assert len(items) == 1
    assert items[0]["chat_id"] == 123456
    assert items[0]["text"] == "Test message"


@pytest.mark.asyncio
async def test_ack_notification_removes_it(ledger):
    """ack_notification must delete the message from the outbox."""
    notif_id = ledger.enqueue_notification(111, "msg1")
    ledger.enqueue_notification(222, "msg2")

    items = ledger.dequeue_notifications()
    assert len(items) == 2

    ledger.ack_notification(notif_id)
    items = ledger.dequeue_notifications()
    assert len(items) == 1
    assert items[0]["chat_id"] == 222


@pytest.mark.asyncio
async def test_retry_notification_backs_off_exponentially(ledger):
    """Each failure must push the retry out further than the previous one; a
    fixed delay re-tries a blocked chat every minute forever."""
    import time

    notif_id = ledger.enqueue_notification(123, "retry me")

    def retries_and_delay():
        row = ledger._conn.execute(
            "SELECT retries, next_retry_at FROM notification_outbox WHERE id = %s",
            (notif_id,),
        ).fetchone()
        return int(row["retries"]), int(row["next_retry_at"]) - int(time.time())

    ledger.retry_notification(notif_id, 30)
    assert ledger.dequeue_notifications() == []  # not due yet
    attempts, delay = retries_and_delay()
    assert attempts == 1
    assert 28 <= delay <= 31

    ledger.retry_notification(notif_id, 30)
    attempts, delay = retries_and_delay()
    assert attempts == 2
    assert 58 <= delay <= 61


@pytest.mark.asyncio
async def test_undeliverable_notification_is_dropped_after_max_retries(ledger):
    """A chat that can never receive the message must not sit at the head of the
    queue forever: after `max_retries` failures the row is deleted."""
    notif_id = ledger.enqueue_notification(999, "never delivered")

    for _ in range(9):
        ledger.retry_notification(notif_id, 1, max_retries=10)
    still_there = ledger._conn.execute(
        "SELECT 1 FROM notification_outbox WHERE id = %s", (notif_id,)
    ).fetchone()
    assert still_there is not None

    ledger.retry_notification(notif_id, 1, max_retries=10)
    gone = ledger._conn.execute(
        "SELECT 1 FROM notification_outbox WHERE id = %s", (notif_id,)
    ).fetchone()
    assert gone is None
