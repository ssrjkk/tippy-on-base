"""On-chain credit scoring — history-based reputation for P2P micro-lending.

Analyzes user behavior to generate a credit score (300-850) that enables
trustless P2P lending without collateral. Uses multiple factors:
- Payment history (40%)
- Account age (20%)
- Transaction volume (15%)
- Market participation (15%)
- Social connections (10%)
"""

import time
from dataclasses import dataclass


@dataclass
class CreditScore:
    user_tg_id: int
    score: int  # 300-850
    grade: str  # A, B, C, D, F
    factors: dict[str, float]
    calculated_at: float
    confidence: float  # 0.0-1.0


class CreditScorer:
    """Calculate and track credit scores based on on-chain behavior."""

    SCORE_MIN = 300
    SCORE_MAX = 850

    def __init__(self, ledger):
        self._ledger = ledger
        self._cache: dict[int, CreditScore] = {}
        self._cache_ttl = 3600  # 1 hour

    async def calculate(self, user_tg_id: int) -> CreditScore:
        """Calculate credit score for user."""
        cached = self._cache.get(user_tg_id)
        if cached and time.time() - cached.calculated_at < self._cache_ttl:
            return cached

        factors = await self._analyze_factors(user_tg_id)
        score = self._compute_score(factors)
        grade = self._score_to_grade(score)
        confidence = self._calculate_confidence(factors)

        credit = CreditScore(
            user_tg_id=user_tg_id,
            score=score,
            grade=grade,
            factors=factors,
            calculated_at=time.time(),
            confidence=confidence,
        )
        self._cache[user_tg_id] = credit
        return credit

    async def _analyze_factors(self, user_tg_id: int) -> dict[str, float]:
        """Analyze user behavior across multiple dimensions."""
        factors = {}

        factors["payment_history"] = await self._score_payment_history(user_tg_id)
        factors["account_age"] = await self._score_account_age(user_tg_id)
        factors["transaction_volume"] = await self._score_volume(user_tg_id)
        factors["market_participation"] = await self._score_markets(user_tg_id)
        factors["social_connections"] = await self._score_social(user_tg_id)

        return factors

    async def _score_payment_history(self, user_tg_id: int) -> float:
        """Score based on successful transactions (0-100)."""
        stats = await self._ledger.user_stats(user_tg_id)
        tx_count = stats.get("total_transactions", 0)
        if tx_count == 0:
            return 0.0

        failed = stats.get("failed_transactions", 0)
        success_rate = (tx_count - failed) / tx_count
        return success_rate * 100

    async def _score_account_age(self, user_tg_id: int) -> float:
        """Score based on account age (0-100)."""
        created = await self._ledger.get_user_created(user_tg_id)
        if not created:
            return 0.0

        age_days = (time.time() - created) / 86400
        if age_days >= 365:
            return 100.0
        elif age_days >= 180:
            return 80.0
        elif age_days >= 90:
            return 60.0
        elif age_days >= 30:
            return 40.0
        else:
            return age_days / 30 * 40

    async def _score_volume(self, user_tg_id: int) -> float:
        """Score based on transaction volume (0-100)."""
        stats = await self._ledger.user_stats(user_tg_id)
        total_volume_micro = stats.get("total_volume_micro", 0)
        total_volume_usd = total_volume_micro / 1e6

        if total_volume_usd >= 10000:
            return 100.0
        elif total_volume_usd >= 5000:
            return 80.0
        elif total_volume_usd >= 1000:
            return 60.0
        elif total_volume_usd >= 100:
            return 40.0
        else:
            return total_volume_usd / 100 * 40

    async def _score_markets(self, user_tg_id: int) -> float:
        """Score based on prediction market participation (0-100)."""
        markets_created = await self._ledger.count_markets_created(user_tg_id)
        markets_participated = await self._ledger.count_markets_participated(user_tg_id)

        total = markets_created + markets_participated
        if total >= 50:
            return 100.0
        elif total >= 20:
            return 80.0
        elif total >= 10:
            return 60.0
        elif total >= 5:
            return 40.0
        else:
            return total / 5 * 40

    async def _score_social(self, user_tg_id: int) -> float:
        """Score based on social connections (unique counterparties) (0-100)."""
        unique_peers = await self._ledger.count_unique_peers(user_tg_id)
        if unique_peers >= 20:
            return 100.0
        elif unique_peers >= 10:
            return 80.0
        elif unique_peers >= 5:
            return 60.0
        elif unique_peers >= 2:
            return 40.0
        else:
            return unique_peers / 2 * 40

    def _compute_score(self, factors: dict[str, float]) -> int:
        """Compute final score (300-850) from weighted factors."""
        weights = {
            "payment_history": 0.40,
            "account_age": 0.20,
            "transaction_volume": 0.15,
            "market_participation": 0.15,
            "social_connections": 0.10,
        }

        weighted_sum = sum(factors[k] * weights[k] for k in weights)
        score_range = self.SCORE_MAX - self.SCORE_MIN
        return int(self.SCORE_MIN + (weighted_sum / 100) * score_range)

    def _score_to_grade(self, score: int) -> str:
        """Convert score to letter grade."""
        if score >= 750:
            return "A"
        elif score >= 650:
            return "B"
        elif score >= 550:
            return "C"
        elif score >= 450:
            return "D"
        else:
            return "F"

    def _calculate_confidence(self, factors: dict[str, float]) -> float:
        """Calculate confidence in score (0-1)."""
        non_zero = sum(1 for v in factors.values() if v > 0)
        return non_zero / len(factors)

    async def get_max_loan(self, user_tg_id: int) -> float:
        """Calculate maximum loan amount based on credit score."""
        credit = await self.calculate(user_tg_id)
        base_limit = 100.0  # $100 base

        if credit.grade == "A":
            multiplier = 10.0
        elif credit.grade == "B":
            multiplier = 5.0
        elif credit.grade == "C":
            multiplier = 2.5
        elif credit.grade == "D":
            multiplier = 1.5
        else:
            multiplier = 0.5

        return base_limit * multiplier * credit.confidence
