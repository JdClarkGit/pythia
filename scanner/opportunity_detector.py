"""Merge-arbitrage opportunity detector.

Scans active markets, fetches order books, applies fee logic, and
yields :class:`Opportunity` objects sorted by descending net profit.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from scanner.clob_client import CLOBClient, OrderBook
from scanner.fee_calculator import FeeCalculator, MarketCategory, classify_market
from scanner.gamma_client import GammaClient, MarketInfo
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class Opportunity:
    """A detected merge-arb opportunity.

    Attributes:
        market: Source market metadata.
        category: Classified fee tier.
        yes_ask: Best YES ask price.
        no_ask: Best NO ask price.
        yes_book: Full YES order book.
        no_book: Full NO order book.
        gross_profit_pct: (1 - yes_ask - no_ask) as a percentage.
        net_profit_pct: Gross less estimated fees as a percentage.
        net_profit_per_share: USDC profit per share-pair.
        max_shares: Liquidity-limited maximum share count.
    """

    market: MarketInfo
    category: MarketCategory
    yes_ask: float
    no_ask: float
    yes_book: OrderBook
    no_book: OrderBook
    gross_profit_pct: float
    net_profit_pct: float
    net_profit_per_share: float
    max_shares: float = 0.0

    def __post_init__(self) -> None:
        if self.max_shares == 0.0:
            self.max_shares = self._liquidity_cap()

    def _liquidity_cap(self) -> float:
        """Estimate maximum tradeable shares based on best-ask depth."""
        yes_depth = self.yes_book.ask_depth_usdc / max(self.yes_ask, 1e-9)
        no_depth = self.no_book.ask_depth_usdc / max(self.no_ask, 1e-9)
        return min(yes_depth, no_depth)

    def profit_for_size(self, shares: float) -> float:
        """Estimated net USDC profit for *shares* share-pairs.

        Args:
            shares: Number of share-pairs to buy.

        Returns:
            Net profit in USDC.
        """
        return self.net_profit_per_share * shares


class OpportunityDetector:
    """Scans all active markets and returns profitable merge-arb opportunities.

    Args:
        gamma: :class:`~scanner.gamma_client.GammaClient` instance.
        clob: :class:`~scanner.clob_client.CLOBClient` instance.
        fee_calc: :class:`~scanner.fee_calculator.FeeCalculator` instance.
        min_profit_pct: Minimum net profit threshold (e.g. ``0.10`` = 0.1 %).
        max_trade_size_usdc: Cap on trade size in USDC.
        use_maker: Whether orders will be placed as makers (no taker fees).
        concurrency: Maximum simultaneous CLOB book requests.
    """

    def __init__(
        self,
        gamma: GammaClient,
        clob: CLOBClient,
        fee_calc: Optional[FeeCalculator] = None,
        *,
        min_profit_pct: float = 0.10,
        max_trade_size_usdc: float = 100.0,
        use_maker: bool = True,
        concurrency: int = 20,
    ) -> None:
        self._gamma = gamma
        self._clob = clob
        self._fee = fee_calc or FeeCalculator()
        self._min_profit_pct = min_profit_pct
        self._max_size = max_trade_size_usdc
        self._maker = use_maker
        self._sem = asyncio.Semaphore(concurrency)

    async def scan(self, markets: Optional[list[MarketInfo]] = None) -> list[Opportunity]:
        """Run a full scan and return sorted profitable opportunities.

        Args:
            markets: Pre-fetched list of markets.  If ``None``, fetches
                     all active markets from the Gamma API.

        Returns:
            List of :class:`Opportunity` sorted by descending net profit %.
        """
        if markets is None:
            markets = await self._gamma.get_all_active_markets()

        log.info("Scanning %d markets for merge-arb opportunities …", len(markets))
        tasks = [self._check_market(m) for m in markets if _has_both_tokens(m)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        opportunities: list[Opportunity] = []
        for r in results:
            if isinstance(r, Opportunity):
                opportunities.append(r)
            elif isinstance(r, Exception):
                log.debug("Market check error: %s", r)

        opportunities.sort(key=lambda o: o.net_profit_pct, reverse=True)
        log.info(
            "Found %d profitable opportunities (min %.2f%%)",
            len(opportunities),
            self._min_profit_pct,
        )
        return opportunities

    async def _check_market(self, market: MarketInfo) -> Optional[Opportunity]:
        """Evaluate a single market for merge-arb viability.

        Args:
            market: Market to check.

        Returns:
            :class:`Opportunity` if profitable, else ``None``.

        Raises:
            Exception: Propagated to the gather() call in :meth:`scan`.
        """
        yes_id = market.yes_token_id
        no_id = market.no_token_id
        if yes_id is None or no_id is None:
            return None

        async with self._sem:
            yes_book, no_book = await asyncio.gather(
                self._clob.get_order_book(yes_id),
                self._clob.get_order_book(no_id),
            )

        if yes_book is None or no_book is None:
            return None

        yes_ask = yes_book.best_ask
        no_ask = no_book.best_ask
        if yes_ask is None or no_ask is None:
            return None

        category = classify_market(
            market.question, tags=market.tags, category=market.category
        )

        profitable = self._fee.is_profitable(
            yes_ask,
            no_ask,
            category,
            min_profit_pct=self._min_profit_pct,
            use_maker=self._maker,
        )
        if not profitable:
            return None

        gross_pct = (1.0 - yes_ask - no_ask) * 100.0
        net_per_share = self._fee.net_profit_per_share(
            yes_ask, no_ask, category, use_maker=self._maker
        )
        net_pct = net_per_share * 100.0

        opp = Opportunity(
            market=market,
            category=category,
            yes_ask=yes_ask,
            no_ask=no_ask,
            yes_book=yes_book,
            no_book=no_book,
            gross_profit_pct=gross_pct,
            net_profit_pct=net_pct,
            net_profit_per_share=net_per_share,
        )
        log.info(
            "OPPORTUNITY | %s | YES %.4f + NO %.4f | net %.4f%%",
            market.question[:60],
            yes_ask,
            no_ask,
            net_pct,
        )
        return opp


def _has_both_tokens(market: MarketInfo) -> bool:
    """Return True if the market has both YES and NO token IDs."""
    return market.yes_token_id is not None and market.no_token_id is not None
