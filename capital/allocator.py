"""Kelly-criterion capital allocator for merge-arb and mean-reversion positions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from scanner.opportunity_detector import Opportunity


@dataclass
class EnhancedAllocationResult:
    """Output of the enhanced Kelly allocator.

    Attributes:
        usdc_amount: Total USDC to deploy.
        kelly_fraction: Raw enhanced Kelly fraction (before fractional scaling).
        p_execution: Estimated probability both legs fill at expected prices.
    """

    usdc_amount: float
    kelly_fraction: float
    p_execution: float


@dataclass
class AllocationResult:
    """Output of the Kelly allocator.

    Attributes:
        shares: Number of share-pairs to purchase.
        usdc_amount: Total USDC to deploy.
        kelly_fraction: Raw Kelly fraction before clamping.
    """

    shares: float
    usdc_amount: float
    kelly_fraction: float


class KellyAllocator:
    """Fractional-Kelly position sizer for binary outcome bets.

    For merge arb the edge (``b``) is the net profit per dollar risked,
    and ``p`` (win probability) is treated as 1.0 since the profit is
    locked in once both legs fill.

    Args:
        kelly_fraction: Scale factor applied to the raw Kelly fraction
                        (``0.25`` = quarter-Kelly — recommended).
        max_allocation_pct: Maximum fraction of available capital per trade.
        min_trade_usdc: Minimum trade size in USDC (avoid dust).
        max_trade_usdc: Hard cap on a single trade in USDC.
    """

    def __init__(
        self,
        kelly_fraction: float = 0.25,
        max_allocation_pct: float = 0.30,
        min_trade_usdc: float = 1.0,
        max_trade_usdc: float = 100.0,
    ) -> None:
        self._kelly = kelly_fraction
        self._max_pct = max_allocation_pct
        self._min = min_trade_usdc
        self._max = max_trade_usdc

    def allocate(
        self,
        opportunity: "Opportunity",
        available_usdc: float,
    ) -> AllocationResult:
        """Compute position size for a merge-arb opportunity.

        The Kelly formula for a guaranteed-win bet (p=1) simplifies to:
        ``f* = edge / (1 + edge)``
        where ``edge = net_profit_per_share / cost_per_share``.

        Args:
            opportunity: Detected :class:`~scanner.opportunity_detector.Opportunity`.
            available_usdc: Free USDC available for deployment.

        Returns:
            :class:`AllocationResult` with the recommended position size.
        """
        cost_per_share = opportunity.yes_ask + opportunity.no_ask
        edge = opportunity.net_profit_per_share / max(cost_per_share, 1e-9)

        # Kelly fraction (capped at 1 to avoid absurdity)
        raw_kelly = min(edge / (1.0 + edge), 1.0)
        scaled_kelly = raw_kelly * self._kelly

        # Capital limits
        max_by_pct = available_usdc * self._max_pct
        max_by_cap = self._max
        target_usdc = min(available_usdc * scaled_kelly, max_by_pct, max_by_cap)
        target_usdc = max(target_usdc, 0.0)

        # Clamp to actual available capital
        target_usdc = min(target_usdc, available_usdc)

        if target_usdc < self._min:
            return AllocationResult(shares=0.0, usdc_amount=0.0, kelly_fraction=scaled_kelly)

        # Convert USDC budget to share count
        shares = target_usdc / max(cost_per_share, 1e-9)

        # Liquidity cap
        shares = min(shares, opportunity.max_shares)
        usdc_amount = shares * cost_per_share

        if usdc_amount < self._min:
            return AllocationResult(shares=0.0, usdc_amount=0.0, kelly_fraction=scaled_kelly)

        return AllocationResult(
            shares=shares,
            usdc_amount=usdc_amount,
            kelly_fraction=scaled_kelly,
        )


def enhanced_kelly(
    b: float,
    p: float,
    *,
    depth_yes: float = 0.0,
    depth_no: float = 0.0,
    trade_size_usdc: float = 1.0,
    fractional: float = 0.25,
    max_usdc: float = 0.0,
    available_usdc: float = 0.0,
) -> EnhancedAllocationResult:
    """Execution-risk-adjusted Kelly criterion.

    Standard Kelly: ``f = (b*p - q) / b``
    Enhanced:       ``f = (b*p - q) / b * sqrt(p_execution)``

    where ``p_execution`` = probability both legs fill at expected prices,
    approximated from order-book depth vs desired trade size.

    Args:
        b: Expected profit as a fraction of the bet
           (e.g. ``8.0`` for an 800 % return from 10 c → 90 c).
        p: Win probability (e.g. ``0.06`` for deep-underdog reversion).
        depth_yes: USDC depth available at the YES target price.
        depth_no: USDC depth available at the NO target price (0 for single-leg).
        trade_size_usdc: Intended trade size in USDC (used to compute p_execution).
        fractional: Fractional-Kelly multiplier (``0.25`` = quarter-Kelly).
        max_usdc: Hard cap on the bet size. ``0`` = uncapped.
        available_usdc: Total free capital; result is clamped to this.

    Returns:
        :class:`EnhancedAllocationResult` with the recommended USDC amount.
    """
    if b <= 0 or p <= 0 or p >= 1:
        return EnhancedAllocationResult(usdc_amount=0.0, kelly_fraction=0.0, p_execution=0.0)

    q = 1.0 - p
    raw_kelly = (b * p - q) / b

    if raw_kelly <= 0:
        # Negative EV — do not bet
        return EnhancedAllocationResult(usdc_amount=0.0, kelly_fraction=raw_kelly, p_execution=0.0)

    # Execution probability: min depth / trade size, clamped [0, 1]
    if trade_size_usdc > 0 and (depth_yes > 0 or depth_no > 0):
        min_depth = min(depth_yes, depth_no) if depth_no > 0 else depth_yes
        p_execution = min(min_depth / trade_size_usdc, 1.0)
    else:
        p_execution = 1.0  # assume fills if no depth data

    # Enhanced Kelly
    adjusted_kelly = raw_kelly * math.sqrt(p_execution) * fractional

    # Cap at 50 % of the shallower book side (never move the market beyond half)
    if depth_yes > 0 or depth_no > 0:
        min_depth = min(depth_yes, depth_no) if depth_no > 0 else depth_yes
        book_cap = min_depth * 0.50
    else:
        book_cap = float("inf")

    target_usdc = available_usdc * adjusted_kelly
    target_usdc = min(target_usdc, book_cap)
    if max_usdc > 0:
        target_usdc = min(target_usdc, max_usdc)
    target_usdc = min(target_usdc, available_usdc)
    target_usdc = max(target_usdc, 0.0)

    return EnhancedAllocationResult(
        usdc_amount=target_usdc,
        kelly_fraction=adjusted_kelly,
        p_execution=p_execution,
    )
