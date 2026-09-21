"""Revenue sharing tokens — creator economy with automatic dividend distribution.

Creators issue tokens that represent a share of their future revenue.
Holders automatically receive proportional dividends when the creator
earns income (tips, market winnings, etc).

This is a breakthrough for Base — enables creator economy natively on-chain.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot import config


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
    """Manage creator tokens and dividend distribution."""

    def __init__(self, state_dir: str):
        self._state_dir = Path(state_dir)
        self._tokens: dict[str, CreatorToken] = {}
        self._holders: dict[str, dict[int, TokenHolder]] = {}  # token_id -> {tg_id -> holder}
        self._dividends: list[DividendRecord] = []
        self._load()

    def _load(self) -> None:
        import json
        tokens_path = self._state_dir / "creator_tokens.json"
        holders_path = self._state_dir / "token_holders.json"

        if tokens_path.exists():
            try:
                data = json.loads(tokens_path.read_text())
                for item in data:
                    self._tokens[item["token_id"]] = CreatorToken(**item)
            except Exception:
                pass

        if holders_path.exists():
            try:
                data = json.loads(holders_path.read_text())
                for token_id, holders_dict in data.items():
                    self._holders[token_id] = {
                        int(k): TokenHolder(**v) for k, v in holders_dict.items()
                    }
            except Exception:
                pass

    def _save(self) -> None:
        import json
        tokens_path = self._state_dir / "creator_tokens.json"
        holders_path = self._state_dir / "token_holders.json"

        tokens_data = [t.__dict__ for t in self._tokens.values()]
        tokens_path.write_text(json.dumps(tokens_data, indent=2))

        holders_data = {}
        for token_id, holders in self._holders.items():
            holders_data[token_id] = {
                str(k): v.__dict__ for k, v in holders.items()
            }
        holders_path.write_text(json.dumps(holders_data, indent=2))

    async def create_token(
        self,
        creator_tg_id: int,
        name: str,
        symbol: str,
        total_supply: int,
        initial_price_micro: int,
    ) -> CreatorToken:
        """Create a new creator token."""
        token_id = f"ct_{creator_tg_id}_{int(time.time())}"
        token = CreatorToken(
            token_id=token_id,
            creator_tg_id=creator_tg_id,
            name=name,
            symbol=symbol,
            total_supply=total_supply,
            price_micro=initial_price_micro,
            created_at=time.time(),
        )
        self._tokens[token_id] = token
        self._holders[token_id] = {}
        self._save()
        return token

    async def buy_tokens(
        self,
        token_id: str,
        buyer_tg_id: int,
        amount: int,
    ) -> tuple[bool, int]:
        """Buy creator tokens. Returns (success, cost_micro)."""
        token = self._tokens.get(token_id)
        if not token:
            return False, 0

        cost_micro = (amount * token.price_micro) // 1000000  # Convert to micro

        if token_id not in self._holders:
            self._holders[token_id] = {}

        if buyer_tg_id not in self._holders[token_id]:
            self._holders[token_id][buyer_tg_id] = TokenHolder(
                token_id=token_id,
                holder_tg_id=buyer_tg_id,
                balance=0,
            )

        holder = self._holders[token_id][buyer_tg_id]
        holder.balance += amount

        self._save()
        return True, cost_micro

    async def sell_tokens(
        self,
        token_id: str,
        seller_tg_id: int,
        amount: int,
    ) -> tuple[bool, int]:
        """Sell creator tokens. Returns (success, proceeds_micro)."""
        token = self._tokens.get(token_id)
        if not token:
            return False, 0

        holders = self._holders.get(token_id, {})
        holder = holders.get(seller_tg_id)
        if not holder or holder.balance < amount:
            return False, 0

        proceeds_micro = (amount * token.price_micro) // 1000000
        holder.balance -= amount

        self._save()
        return True, proceeds_micro

    async def distribute_dividend(
        self,
        token_id: str,
        amount_micro: int,
    ) -> Optional[DividendRecord]:
        """Distribute dividend to all token holders.

        Called when creator earns revenue. Automatically distributes
        proportionally to all holders.
        """
        token = self._tokens.get(token_id)
        if not token:
            return None

        holders = self._holders.get(token_id, {})
        if not holders:
            return None

        total_held = sum(h.balance for h in holders.values())
        if total_held == 0:
            return None

        dividend_per_token = amount_micro / total_held

        for holder in holders.values():
            holder.pending_dividends_micro += int(holder.balance * dividend_per_token)

        token.total_revenue_micro += amount_micro
        token.total_dividends_paid_micro += amount_micro
        token.dividend_per_token_micro += dividend_per_token

        record = DividendRecord(
            token_id=token_id,
            amount_micro=amount_micro,
            dividend_per_token=dividend_per_token,
            timestamp=time.time(),
            total_holders=len(holders),
        )
        self._dividends.append(record)

        self._save()
        return record

    async def claim_dividends(
        self,
        token_id: str,
        holder_tg_id: int,
    ) -> int:
        """Claim pending dividends. Returns amount claimed."""
        holders = self._holders.get(token_id, {})
        holder = holders.get(holder_tg_id)
        if not holder:
            return 0

        amount = holder.pending_dividends_micro
        holder.pending_dividends_micro = 0
        holder.last_dividend_claim = time.time()

        self._save()
        return amount

    async def get_holder_info(
        self,
        token_id: str,
        holder_tg_id: int,
    ) -> Optional[dict]:
        """Get holder's token balance and pending dividends."""
        holders = self._holders.get(token_id, {})
        holder = holders.get(holder_tg_id)
        if not holder:
            return None

        return {
            "balance": holder.balance,
            "pending_dividends_micro": holder.pending_dividends_micro,
            "last_claim": holder.last_dividend_claim,
        }

    async def get_token_info(self, token_id: str) -> Optional[dict]:
        """Get token metadata and stats."""
        token = self._tokens.get(token_id)
        if not token:
            return None

        holders = self._holders.get(token_id, {})
        holder_count = len(holders)
        total_held = sum(h.balance for h in holders.values())

        return {
            "token_id": token.token_id,
            "creator_tg_id": token.creator_tg_id,
            "name": token.name,
            "symbol": token.symbol,
            "total_supply": token.total_supply,
            "price_micro": token.price_micro,
            "holder_count": holder_count,
            "total_held": total_held,
            "total_revenue_micro": token.total_revenue_micro,
            "total_dividends_paid_micro": token.total_dividends_paid_micro,
        }

    async def list_creator_tokens(self, creator_tg_id: int) -> list[CreatorToken]:
        """List all tokens created by a user."""
        return [t for t in self._tokens.values() if t.creator_tg_id == creator_tg_id]
