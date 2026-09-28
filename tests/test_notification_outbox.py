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
async def test_retry_notification_increments_backoff(ledger):
    """retry_notification must increment retries and schedule a future retry."""
    import time

    notif_id = ledger.enqueue_notification(123, "retry me")
    ledger.retry_notification(notif_id, 30)

    # Should not be due yet (next_retry_at is in the future).
    items = ledger.dequeue_notifications()
    assert len(items) == 0

    # Manually check the row to verify retries incremented.
    row = ledger._conn.execute(
        "SELECT retries, next_retry_at FROM notification_outbox WHERE id = %s",
        (notif_id,),
    ).fetchone()
    assert int(row["retries"]) == 1
    assert int(row["next_retry_at"]) > int(time.time())
