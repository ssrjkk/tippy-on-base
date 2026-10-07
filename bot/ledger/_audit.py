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

    def query_audit_logs(
        self,
        user_id: int | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query audit logs with optional filters.

        Returns list of dicts with keys: id, created_at, user_id, action,
        resource, metadata, ip_address, success.
        """
        conditions = []
        params = []

        if user_id is not None:
            conditions.append("user_id = %s")
            params.append(user_id)
        if action is not None:
            conditions.append("action = %s")
            params.append(action)

        where = " AND ".join(conditions) if conditions else "TRUE"
        params.extend([limit, offset])

        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, created_at, user_id, action, resource, metadata, ip_address, success
                FROM audit_logs
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                params,
            ).fetchall()

        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "user_id": r["user_id"],
                "action": r["action"],
                "resource": r["resource"],
                "metadata": r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"] or "{}"),
                "ip_address": r["ip_address"],
                "success": r["success"],
            }
            for r in rows
        ]
