"""Base Paymaster integration — gasless onboarding for new users.

New users get FREE_TRANSACTIONS_COUNT gasless transactions.
Uses Base Paymaster API for ERC-4337 UserOperations.
"""

import asyncio
import os
from dataclasses import dataclass

import httpx

FREE_TRANSACTIONS_COUNT = int(os.environ.get("PAYMASTER_FREE_TX", "10"))
PAYMASTER_URL = os.environ.get("PAYMASTER_URL", "https://api.pimlico.io/v2/base/rpc")
PAYMASTER_API_KEY = os.environ.get("PAYMASTER_API_KEY", "")


@dataclass
class PaymasterState:
    """Track gasless usage per user."""
    user_address: str
    used_count: int = 0
    last_used: float = 0.0

    @property
    def remaining(self) -> int:
        return max(0, FREE_TRANSACTIONS_COUNT - self.used_count)

    @property
    def eligible(self) -> bool:
        return self.remaining > 0


def get_state(user_address: str) -> PaymasterState:
    """Get paymaster state for user from persisted store."""
    from bot import ledger as ledger_mod
    addr = user_address.lower()
    row = ledger_mod.ledger.paymaster_get_usage(addr)
    return PaymasterState(
        user_address=addr,
        used_count=row["used_count"],
        last_used=float(row["last_used"]),
    )


def increment_usage(user_address: str) -> int:
    """Record gasless transaction usage. Returns new total."""
    from bot import ledger as ledger_mod
    return ledger_mod.ledger.paymaster_increment(user_address)


async def sponsor_user_operation(
    user_op: dict,
    entry_point: str,
    user_address: str,
) -> dict | None:
    """Request paymaster sponsorship for UserOperation.

    Returns paymasterData if user is eligible, None otherwise.
    """
    state = await asyncio.to_thread(get_state, user_address)
    if not state.eligible:
        return None

    if not PAYMASTER_API_KEY:
        return None

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                PAYMASTER_URL,
                json={
                    "jsonrpc": "2.0",
                    "method": "pm_sponsorUserOperation",
                    "params": [
                        user_op,
                        entry_point,
                        {
                            "sponsorshipPolicyId": "sp_tippy_onboarding",
                        }
                    ],
                    "id": 1,
                },
                headers={
                    "api-key": PAYMASTER_API_KEY,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            result = response.json()

            if "result" in result:
                return result["result"]
            return None
        except Exception:
            return None


async def check_eligibility(user_address: str) -> dict:
    """Check if user is eligible for gasless transactions."""
    state = await asyncio.to_thread(get_state, user_address)
    return {
        "eligible": state.eligible,
        "remaining": state.remaining,
        "used": state.used_count,
        "total": FREE_TRANSACTIONS_COUNT,
    }
