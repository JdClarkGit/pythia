"""Polymarket taker-fee calculator.

Fee tiers (taker only — makers pay zero and earn rebates):
  - Most markets (politics, news, etc.): 0 %
  - Crypto markets: rate = 2·p·0.25·(p·(1−p))², max ≈ 1.5625 % at p = 0.5
  - NCAAB / Serie A sports: rate = 2·p·0.0175·(p·(1−p))¹, max ≈ 0.4375 % at p = 0.5

Reference: https://docs.polymarket.com/#fees
"""

from __future__ import annotations

import enum
import re
from typing import Optional


class MarketCategory(str, enum.Enum):
    """Coarse fee tier for a Polymarket market."""

    ZERO_FEE = "zero_fee"       # politics, news, most markets
    CRYPTO = "crypto"           # BTC/ETH price markets
    SPORTS_NCAAB = "ncaab"      # NCAAB / Serie A bracket
    SPORTS_OTHER = "sports_other"  # other sports (zero fee currently)


# Keywords used to classify markets by their question text / category tag.
_CRYPTO_KEYWORDS = re.compile(
    r"\b(btc|eth|bitcoin|ethereum|crypto|sol|bnb|xrp|doge|avax|matic|polygon)\b",
    re.IGNORECASE,
)
_NCAAB_KEYWORDS = re.compile(
    r"\b(ncaab|march\s+madness|ncaa\s+basketball|serie\s+a)\b",
    re.IGNORECASE,
)


def classify_market(
    question: str,
    tags: Optional[list[str]] = None,
    category: Optional[str] = None,
) -> MarketCategory:
    """Classify a market into a fee tier.

    Args:
        question: The market question string.
        tags: Optional list of tag strings from the Gamma API.
        category: Optional top-level category string (``"crypto"``, …).

    Returns:
        :class:`MarketCategory` enum value.
    """
    combined = " ".join(filter(None, [question, category, *(tags or [])]))
    if _NCAAB_KEYWORDS.search(combined):
        return MarketCategory.SPORTS_NCAAB
    if _CRYPTO_KEYWORDS.search(combined):
        return MarketCategory.CRYPTO
    return MarketCategory.ZERO_FEE


class FeeCalculator:
    """Compute taker fees for a single token purchase.

    Formulas (all per-token, as a fraction of 1 USDC):
        - ZERO_FEE:      fee_rate = 0
        - CRYPTO:        fee_rate = 2 · p · 0.25 · (p·(1−p))²
        - SPORTS_NCAAB:  fee_rate = 2 · p · 0.0175 · (p·(1−p))
        - SPORTS_OTHER:  fee_rate = 0 (same as ZERO_FEE for now)
    """

    def taker_fee_rate(self, price: float, category: MarketCategory) -> float:
        """Return the fractional taker fee rate for buying at *price*.

        Args:
            price: Token ask price in [0, 1].
            category: :class:`MarketCategory` of the market.

        Returns:
            Fee rate as a fraction (e.g. ``0.01`` = 1 %).
        """
        if price <= 0 or price >= 1:
            return 0.0
        p = price
        q = p * (1.0 - p)  # variance term

        if category == MarketCategory.CRYPTO:
            # max ≈ 1.5625 % at p = 0.5
            return 2.0 * p * 0.25 * (q ** 2)
        if category == MarketCategory.SPORTS_NCAAB:
            # max ≈ 0.4375 % at p = 0.5
            return 2.0 * p * 0.0175 * q
        # ZERO_FEE and SPORTS_OTHER
        return 0.0

    def taker_fee_usdc(
        self, price: float, shares: float, category: MarketCategory
    ) -> float:
        """Return the absolute USDC taker fee for *shares* tokens at *price*.

        Args:
            price: Token ask price in [0, 1].
            shares: Number of tokens being purchased.
            category: :class:`MarketCategory` of the market.

        Returns:
            Fee in USDC.
        """
        rate = self.taker_fee_rate(price, category)
        # Fee is applied to the notional value (price × shares)
        return rate * price * shares

    def total_cost(
        self,
        yes_ask: float,
        no_ask: float,
        shares: float,
        category: MarketCategory,
        *,
        use_maker: bool = False,
    ) -> float:
        """Total USDC cost to acquire *shares* of both YES and NO tokens.

        Args:
            yes_ask: Best ask price for YES token.
            no_ask: Best ask price for NO token.
            shares: Number of share-pairs to buy.
            category: Market fee tier.
            use_maker: If ``True`` taker fees are waived (maker / limit orders).

        Returns:
            Total USDC cost including fees.
        """
        token_cost = (yes_ask + no_ask) * shares
        if use_maker:
            return token_cost
        fee_yes = self.taker_fee_usdc(yes_ask, shares, category)
        fee_no = self.taker_fee_usdc(no_ask, shares, category)
        return token_cost + fee_yes + fee_no

    def net_profit_per_share(
        self,
        yes_ask: float,
        no_ask: float,
        category: MarketCategory,
        *,
        use_maker: bool = False,
    ) -> float:
        """Profit from merging 1 share-pair (USDC in minus $1.00 out).

        Args:
            yes_ask: Best ask price for YES.
            no_ask: Best ask price for NO.
            category: Market fee tier.
            use_maker: Whether orders are placed as makers (no taker fees).

        Returns:
            Net profit per share pair in USDC.  Negative means loss.
        """
        cost = self.total_cost(yes_ask, no_ask, 1.0, category, use_maker=use_maker)
        return 1.0 - cost

    def is_profitable(
        self,
        yes_ask: float,
        no_ask: float,
        category: MarketCategory,
        *,
        min_profit_pct: float = 0.10,
        use_maker: bool = False,
    ) -> bool:
        """Return True if the merge arb meets the minimum profit threshold.

        Args:
            yes_ask: Best ask price for YES.
            no_ask: Best ask price for NO.
            category: Market fee tier.
            min_profit_pct: Minimum net profit as % of $1.00 (e.g. ``0.10`` = 0.1 %).
            use_maker: Whether using maker orders.

        Returns:
            ``True`` if profitable above the threshold.
        """
        profit = self.net_profit_per_share(yes_ask, no_ask, category, use_maker=use_maker)
        return profit >= (min_profit_pct / 100.0)
