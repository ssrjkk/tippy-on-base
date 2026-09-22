"""Revenue sharing tokens — creator economy with automatic dividend distribution.

Creators issue tokens that represent a share of their future revenue.
Holders automatically receive proportional dividends when the creator
earns income (tips, market winnings, etc).

State lives in PostgreSQL (bot.ledger.LedgerCreatorMixin) so it survives
restarts and is covered by the same backups as every other balance. A
pre-existing JSON state (creator_tokens.json / token_holders.json) is
imported once, then archived as *.json.migrated.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CreatorToken:
    token_id: str
    creator_tg_id: int
    name: str
    symbol: str
    total_supply: int  # in micro-units
    price_micro: int  # current price per token
    created_at: float = 0.0

    # Revenue tracking
    total_revenue_micro: int = 0
    total_dividends_paid_micro: int = 0
    dividend_per_token_micro: float = 0.0


@dataclass
class TokenHolder:
    token_id: str
    holder_tg_id: int
    balance: int  # tokens held
    last_dividend_claim: float = 0.0
    pending_dividends_micro: int = 0


@dataclass
class DividendRecord:
    token_id: str
    amount_micro: int
    dividend_per_token: float
    timestamp: float
    total_holders: int


class CreatorTokenRegistry:
    """Manage creator tokens and dividend distribution (PostgreSQL-backed).

    The ledger handle is resolved per call (not bound in __init__) so test
    fixtures can rebind bot.ledger.async_ledger and stay hermetic.
    """

    def __init__(self, state_dir: str):
        self._state_dir = Path(state_dir)
        self._legacy = self._read_legacy()
        self._migrated = False
        self._migrate_lock = asyncio.Lock()

    # ---------- legacy JSON import (one-time) ----------

    def _read_legacy(self) -> tuple[list[dict], dict[str, list[dict]]] | None:
        tokens_path = self._state_dir / "creator_tokens.json"
        holders_path = self._state_dir / "token_holders.json"

        tokens: list[dict] = []
        try:
            if tokens_path.exists():
                tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
        except Exception:
            tokens = []

        holders: dict[str, list[dict]] = {}
        try:
            if holders_path.exists():
                raw = json.loads(holders_path.read_text(encoding="utf-8"))
                holders = {
                    token_id: list(hmap.values())
                    for token_id, hmap in raw.items()
                    if isinstance(hmap, dict)
                }
        except Exception:
            holders = {}

        if not tokens and not holders:
            return None
        return tokens, holders

    def _archive_legacy(self) -> None:
        for name in ("creator_tokens.json", "token_holders.json"):
            p = self._state_dir / name
            if p.exists():
                try:
                    p.rename(p.with_suffix(".json.migrated"))
                except OSError:
                    pass  # emptiness check already prevents re-import

    async def _ensure_migrated(self) -> None:
        if self._migrated:
            return
        async with self._migrate_lock:
            if self._migrated:
                return
            if self._legacy is not None:
                tokens, holders = self._legacy
                await asyncio.to_thread(self._import_legacy, tokens, holders)
            self._migrated = True

    def _import_legacy(self, tokens: list[dict], holders: dict[str, list[dict]]) -> None:
        from . import ledger as ledger_mod

        led = ledger_mod.ledger
        imported = led.creator_legacy_import(tokens, holders)
        if imported or led.creator_tokens_count() > 0:
            self._archive_legacy()

    def _led(self):
        from . import ledger as ledger_mod

        return ledger_mod.async_ledger

    # ---------- API (unchanged signatures) ----------

    async def create_token(
        self,
        creator_tg_id: int,
        name: str,
        symbol: str,
        total_supply: int,
        initial_price_micro: int,
    ) -> CreatorToken:
        """Create a new creator token."""
        token = CreatorToken(
            token_id=f"ct_{creator_tg_id}_{int(time.time())}",
            creator_tg_id=creator_tg_id,
            name=name,
            symbol=symbol,
            total_supply=total_supply,
            price_micro=initial_price_micro,
            created_at=time.time(),
        )
        await self._ensure_migrated()
        row = token.__dict__ | {"created_at": int(token.created_at)}
        await self._led().creator_token_upsert(row)
        return token

    async def buy_tokens(
        self,
        token_id: str,
        buyer_tg_id: int,
        amount: int,
    ) -> tuple[bool, int]:
        """Buy creator tokens. Returns (success, cost_micro)."""
        await self._ensure_migrated()
        led = self._led()
        token = await led.creator_token_get(token_id)
        if not token:
            return False, 0

        cost_micro = (amount * token["price_micro"]) // 1000000  # Convert to micro
        await led.creator_holder_add(token_id, buyer_tg_id, amount)
        return True, cost_micro

    async def sell_tokens(
        self,
        token_id: str,
        seller_tg_id: int,
        amount: int,
    ) -> tuple[bool, int]:
        """Sell creator tokens. Returns (success, proceeds_micro)."""
        await self._ensure_migrated()
        led = self._led()
        token = await led.creator_token_get(token_id)
        if not token:
            return False, 0

        sold = await led.creator_holder_sub(token_id, seller_tg_id, amount)
        if not sold:
            return False, 0

        proceeds_micro = (amount * token["price_micro"]) // 1000000
        return True, proceeds_micro

    async def distribute_dividend(
        self,
        token_id: str,
        amount_micro: int,
    ) -> DividendRecord | None:
        """Distribute dividend to all token holders.

        Called when creator earns revenue. Automatically distributes
        proportionally to all holders.
        """
        await self._ensure_migrated()
        led = self._led()
        token = await led.creator_token_get(token_id)
        if not token:
            return None

        total_held, holder_count = await led.creator_holders_summary(token_id)
        if total_held == 0:
            return None

        dividend_per_token = amount_micro / total_held
        applied = await led.creator_dividend_apply(
            token_id, amount_micro, dividend_per_token, holder_count
        )
        if applied is None:
            return None

        return DividendRecord(
            token_id=token_id,
            amount_micro=amount_micro,
            dividend_per_token=dividend_per_token,
            timestamp=applied["timestamp"],
            total_holders=holder_count,
        )

    async def claim_dividends(
        self,
        token_id: str,
        holder_tg_id: int,
    ) -> int:
        """Claim pending dividends. Returns amount claimed."""
        await self._ensure_migrated()
        return await self._led().creator_holder_claim(token_id, holder_tg_id)

    async def get_holder_info(
        self,
        token_id: str,
        holder_tg_id: int,
    ) -> dict | None:
        """Get holder's token balance and pending dividends."""
        await self._ensure_migrated()
        row = await self._led().creator_holder_get(token_id, holder_tg_id)
        if not row:
            return None

        return {
            "balance": row["balance"],
            "pending_dividends_micro": row["pending_dividends_micro"],
            "last_claim": row["last_dividend_claim"],
        }

    async def get_token_info(self, token_id: str) -> dict | None:
        """Get token metadata and stats."""
        await self._ensure_migrated()
        led = self._led()
        token = await led.creator_token_get(token_id)
        if not token:
            return None

        total_held, holder_count = await led.creator_holders_summary(token_id)

        return {
            "token_id": token["token_id"],
            "creator_tg_id": token["creator_tg_id"],
            "name": token["name"],
            "symbol": token["symbol"],
            "total_supply": token["total_supply"],
            "price_micro": token["price_micro"],
            "holder_count": holder_count,
            "total_held": total_held,
            "total_revenue_micro": token["total_revenue_micro"],
            "total_dividends_paid_micro": token["total_dividends_paid_micro"],
        }

    async def list_creator_tokens(self, creator_tg_id: int) -> list[CreatorToken]:
        """List all tokens created by a user."""
        await self._ensure_migrated()
        rows = await self._led().creator_tokens_by_creator(creator_tg_id)
        return [CreatorToken(**row) for row in rows]


registry = CreatorTokenRegistry(os.environ.get("STATE_DIR", "."))
