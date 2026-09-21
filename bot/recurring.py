"""Recurring payments — automated scheduled transfers via smart accounts.

Users can set up recurring tips/subscriptions that execute automatically
on a schedule (daily, weekly, monthly). Uses cron-style scheduling.
"""

import asyncio
import enum
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot import config


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
    """JSON-file persisted store for recurring payments."""

    def __init__(self, state_dir: str):
        self._path = Path(state_dir) / "recurring_payments.json"
        self._payments: dict[str, RecurringPayment] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        import json
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                for item in data:
                    item["interval"] = RecurrenceInterval(item["interval"])
                    self._payments[item["id"]] = RecurringPayment(**item)
            except Exception:
                self._payments = {}

    def _save(self) -> None:
        import json
        data = []
        for p in self._payments.values():
            d = p.__dict__.copy()
            d["interval"] = d["interval"].value
            data.append(d)
        self._path.write_text(json.dumps(data, indent=2))

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
        async with self._lock:
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
            self._payments[payment_id] = payment
            self._save()
            return payment

    async def cancel(self, payment_id: str, tg_id: int) -> bool:
        async with self._lock:
            p = self._payments.get(payment_id)
            if not p or p.from_tg_id != tg_id:
                return False
            p.active = False
            self._save()
            return True

    async def get_due(self, now: float) -> list[RecurringPayment]:
        return [p for p in self._payments.values() if p.should_execute(now)]

    async def mark_executed(self, payment_id: str) -> None:
        async with self._lock:
            p = self._payments.get(payment_id)
            if not p:
                return
            now = time.time()
            p.last_execution = now
            p.execution_count += 1
            p.next_execution = now + INTERVAL_SECONDS[p.interval]
            if p.max_executions > 0 and p.execution_count >= p.max_executions:
                p.active = False
            self._save()

    async def list_for_user(self, tg_id: int) -> list[RecurringPayment]:
        return [
            p for p in self._payments.values()
            if p.from_tg_id == tg_id or p.to_tg_id == tg_id
        ]

    async def get(self, payment_id: str) -> Optional[RecurringPayment]:
        return self._payments.get(payment_id)
