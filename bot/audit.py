"""Audit log system for critical operations.

Provides database-backed audit logging for compliance, security investigations,
and chargeback resolution. Tracks withdrawals, deposits, admin actions, and
other sensitive operations.
"""

import logging
from typing import Any

from bot.ledger import async_ledger as ledger

log = logging.getLogger(__name__)


async def log_action(
    action: str,
    resource: str,
    user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    success: bool = True,
) -> None:
    """Log an audit entry to the database.

    Args:
        action: Type of action (e.g., "withdraw", "deposit", "admin_freeze")
        resource: Resource identifier (e.g., "usdc", "user:123", "market:456")
        user_id: Telegram ID of the user (if applicable)
        metadata: Additional context (amounts, addresses, etc.)
        ip_address: Client IP address (if applicable)
        success: Whether the action succeeded
    """
    try:
        await ledger.log_audit_entry(
            user_id=user_id,
            action=action,
            resource=resource,
            metadata=metadata or {},
            ip_address=ip_address,
            success=success,
        )
    except Exception:
        log.exception("Failed to write audit log: action=%s resource=%s", action, resource)


async def query_logs(
    user_id: int | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Query audit logs with optional filters.

    Args:
        user_id: Filter by user ID
        action: Filter by action type
        limit: Max entries to return
        offset: Skip first N entries

    Returns:
        List of audit log entries (newest first)
    """
    return await ledger.query_audit_logs(
        user_id=user_id,
        action=action,
        limit=limit,
        offset=offset,
    )
