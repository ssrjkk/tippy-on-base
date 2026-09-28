"""Recurring payments — automated scheduled transfers via smart accounts.

Users can set up recurring tips/subscriptions that execute automatically
on a schedule (daily, weekly, monthly). State lives in PostgreSQL
(bot.ledger.LedgerRecurringMixin) so it survives restarts and is covered
by the same backups as every other balance. A pre-existing JSON state
(recurring_payments.json) is imported once, then archived as *.migrated.

The atomic claim pattern (mark_executed) uses a compare-and-set on
next_execution so two concurrent executor loops cannot double-debit.
"""

import asyncio
import enum
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


class RecurrenceInterval(enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


INTERVAL_SECONDS = {
    RecurrenceInterval.DAILY: 86400,
    RecurrenceInterval.WEEKLY: 604800,
    RecurrenceInterval.BIWEEKLY: 1209600,
    RecurrenceInterval.MONTHLY: 2592000,
}


@dataclass
class RecurringPayment:
    id: str
    from_tg_id: int
    to_tg_id: int
    amount_micro: int
    interval: RecurrenceInterval
    memo: str = ""
    active: bool = True
    created_at: float = 0.0
    next_execution: float = 0.0
    last_execution: float = 0.0
    execution_count: int = 0
    max_executions: int = 0  # 0 = unlimited

    def should_execute(self, now: float) -> bool:
        if not self.active:
            return False
        if self.max_executions > 0 and self.execution_count >= self.max_executions:
            return False
        return now >= self.next_execution


class RecurringPaymentStore:
    """PostgreSQL-backed store for recurring payments.

    The ledger handle is resolved per call (not bound in __init__) so test
    fixtures can rebind bot.ledger.async_ledger and stay hermetic.
    """

    def __init__(self, state_dir: str):
        self._state_dir = Path(state_dir)
        self._legacy = self._read_legacy()
        self._migrated = False
        self._migrate_lock = asyncio.Lock()

    def _read_legacy(self) -> list[dict] | None:
        path = self._state_dir / "recurring_payments.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        return data

    def _archive_legacy(self) -> None:
        path = self._state_dir / "recurring_payments.json"
        if path.exists():
            try:
                path.rename(path.with_suffix(".json.migrated"))
            except OSError:
                pass

    async def _ensure_migrated(self) -> None:
        if self._migrated:
            return
        async with self._migrate_lock:
            if self._migrated:
                return
            if self._legacy is not None:
                await asyncio.to_thread(self._import_legacy, self._legacy)
            self._migrated = True

    def _import_legacy(self, payments: list[dict]) -> None:
        from . import ledger as ledger_mod

        led = ledger_mod.ledger
        rows = []
        for item in payments:
            if not isinstance(item, dict) or "id" not in item:
                continue
            row = {
                "id": item["id"],
                "from_tg_id": int(item["from_tg_id"]),
                "to_tg_id": int(item["to_tg_id"]),
                "amount_micro": int(item["amount_micro"]),
                "interval": item["interval"],
                "memo": item.get("memo", ""),
                "active": bool(item.get("active", True)),
                "created_at": int(item.get("created_at", 0)),
                "next_execution": int(item.get("next_execution", 0)),
                "last_execution": int(item.get("last_execution", 0)),
                "execution_count": int(item.get("execution_count", 0)),
                "max_executions": int(item.get("max_executions", 0)),
            }
            rows.append(row)
        imported = led.recurring_legacy_import(rows)
        if imported or self._count() > 0:
            self._archive_legacy()

    def _count(self) -> int:
        from . import ledger as ledger_mod

        led = ledger_mod.ledger
        with led._lock:
            row = led._conn.execute(
                "SELECT COUNT(*) AS n FROM recurring_payments"
            ).fetchone()
        return int(row["n"])

    def _led(self):
        from . import ledger as ledger_mod

        return ledger_mod.async_ledger

    @staticmethod
    def _row_to_payment(row: dict) -> RecurringPayment:
        return RecurringPayment(
            id=row["id"],
            from_tg_id=int(row["from_tg_id"]),
            to_tg_id=int(row["to_tg_id"]),
            amount_micro=int(row["amount_micro"]),
            interval=RecurrenceInterval(row["interval"]),
            memo=row.get("memo", ""),
            active=bool(row.get("active", True)),
            created_at=float(row.get("created_at", 0)),
            next_execution=float(row.get("next_execution", 0)),
            last_execution=float(row.get("last_execution", 0)),
            execution_count=int(row.get("execution_count", 0)),
            max_executions=int(row.get("max_executions", 0)),
        )

    async def create(
        self,
        payment_id: str,
        from_tg_id: int,
        to_tg_id: int,
        amount_micro: int,
        interval: RecurrenceInterval,
        memo: str = "",
        max_executions: int = 0,
    ) -> RecurringPayment:
        await self._ensure_migrated()
        now = time.time()
        payment = RecurringPayment(
            id=payment_id,
            from_tg_id=from_tg_id,
            to_tg_id=to_tg_id,
            amount_micro=amount_micro,
            interval=interval,
            memo=memo,
            created_at=now,
            next_execution=now + INTERVAL_SECONDS[interval],
            max_executions=max_executions,
        )
        row = {
            "id": payment.id,
            "from_tg_id": payment.from_tg_id,
            "to_tg_id": payment.to_tg_id,
            "amount_micro": payment.amount_micro,
            "interval": payment.interval.value,
            "memo": payment.memo,
            "active": payment.active,
            "created_at": int(payment.created_at),
            "next_execution": int(payment.next_execution),
            "last_execution": int(payment.last_execution),
            "execution_count": payment.execution_count,
            "max_executions": payment.max_executions,
        }
        await self._led().recurring_insert(row)
        return payment

    async def cancel(self, payment_id: str, tg_id: int) -> bool:
        await self._ensure_migrated()
        return await self._led().recurring_cancel(payment_id, tg_id)

    async def get_due(self, now: float) -> list[RecurringPayment]:
        await self._ensure_migrated()
        rows = await self._led().recurring_get_due(int(now))
        return [self._row_to_payment(r) for r in rows]

    async def mark_executed(self, payment_id: str) -> None:
        await self._ensure_migrated()
        row = await self._led().recurring_get(payment_id)
        if not row:
            return
        interval = RecurrenceInterval(row["interval"])
        await self._led().recurring_mark_executed(payment_id, INTERVAL_SECONDS[interval])

    async def list_for_user(self, tg_id: int) -> list[RecurringPayment]:
        await self._ensure_migrated()
        rows = await self._led().recurring_list_for_user(tg_id)
        return [self._row_to_payment(r) for r in rows]

    async def get(self, payment_id: str) -> RecurringPayment | None:
        await self._ensure_migrated()
        row = await self._led().recurring_get(payment_id)
        if not row:
            return None
        return self._row_to_payment(row)


# Process-wide store: handlers (bot.handlers._common) and the executor in
# bot.main share one instance. The store is now backed by PostgreSQL, so
# there is no file-level race — the DB serializes concurrent writes.
store = RecurringPaymentStore(os.environ.get("STATE_DIR", "."))
