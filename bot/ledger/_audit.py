"""Ledger domain mixin: LedgerAuditMixin (audit log operations)."""

import json


class LedgerAuditMixin:
    def log_audit_entry(
        self,
        user_id: int | None,
        action: str,
        resource: str,
        metadata: dict | None = None,
        ip_address: str | None = None,
        success: bool = True,
    ) -> None:
        """Insert an audit log entry.

        Args:
            user_id: Telegram ID (nullable for system actions)
            action: Action type (e.g., "withdraw", "deposit", "admin_freeze")
            resource: Resource identifier (e.g., "usdc", "user:123")
            metadata: JSON-serializable context dict
            ip_address: Client IP (if applicable)
            success: Whether the action succeeded
        """
        metadata_json = json.dumps(metadata or {})
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO audit_logs (user_id, action, resource, metadata, ip_address, success)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                """,
                (user_id, action, resource, metadata_json, ip_address, success),
            )
            self._conn.commit()
