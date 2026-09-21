"""Base Paymaster integration — gasless onboarding for new users.

New users get FREE_TRANSACTIONS_COUNT gasless transactions.
Uses Base Paymaster API for ERC-4337 UserOperations.
"""

import os
import time
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


_states: dict[str, PaymasterState] = {}


def get_state(user_address: str) -> PaymasterState:
    """Get or create paymaster state for user."""
    addr = user_address.lower()
    if addr not in _states:
        _states[addr] = PaymasterState(user_address=addr)
    return _states[addr]


def increment_usage(user_address: str) -> None:
    """Record gasless transaction usage."""
    state = get_state(user_address)
    state.used_count += 1
    state.last_used = time.time()


async def sponsor_user_operation(
    user_op: dict,
    entry_point: str,
    user_address: str,
) -> dict | None:
    """Request paymaster sponsorship for UserOperation.

    Returns paymasterData if user is eligible, None otherwise.
    """
    state = get_state(user_address)
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
    state = get_state(user_address)
    return {
        "eligible": state.eligible,
        "remaining": state.remaining,
        "used": state.used_count,
        "total": FREE_TRANSACTIONS_COUNT,
    }
