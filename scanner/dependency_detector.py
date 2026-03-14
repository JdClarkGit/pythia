"""Cross-market dependency detector.

Groups markets by shared tags, detects logical price inconsistencies
(e.g. P(B) > P(A) when B ⊆ A is logically required), and surfaces arb
opportunities without requiring Gurobi — uses scipy LP instead.

Dependency types detected:
    SUBSET           — "B implies A" ↔ P(B) ≤ P(A) must hold
    MUTUAL_EXCLUSIVE — P(A) + P(B) > 1 when they are mutually exclusive
    STATE_NATIONAL   — state winner ↔ national winner consistency
    TEAM_TOURNAMENT  — team advances ↔ team wins championship consistency
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

try:
    from scipy.optimize import linprog  # type: ignore[import-untyped]
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False

from db.models import DependencyPair, DependencyType
from scanner.gamma_client import GammaClient, MarketInfo
from utils.logger import get_logger

log = get_logger(__name__)

# Minimum expected arb profit (as fraction of $1) to report a pair
MIN_PROFIT_THRESHOLD = 0.02

# Keyword patterns for dependency classification
_SUBSET_PATTERNS: list[tuple[re.Pattern[str], re.Pattern[str]]] = [
    # "wins by 5+" ⊆ "wins"
    (re.compile(r"\bwins?\b", re.I), re.compile(r"\bwins?\s+by\b", re.I)),
    # "wins popular vote" ⊆ "wins election"
    (re.compile(r"\bwins?\s+(?:the\s+)?election\b", re.I),
     re.compile(r"\bwins?\s+(?:the\s+)?popular\s+vote\b", re.I)),
    # "wins championship" ⊆ "advances to final"
    (re.compile(r"\badvances?\b", re.I), re.compile(r"\bwins?\s+(?:the\s+)?championship\b", re.I)),
]

_STATE_RE = re.compile(
    r"\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|"
    r"delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|"
    r"kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
    r"mississippi|missouri|montana|nebraska|nevada|new\s+hampshire|new\s+jersey|"
    r"new\s+mexico|new\s+york|north\s+carolina|north\s+dakota|ohio|oklahoma|"
    r"oregon|pennsylvania|rhode\s+island|south\s+carolina|south\s+dakota|"
    r"tennessee|texas|utah|vermont|virginia|washington|west\s+virginia|"
    r"wisconsin|wyoming)\b",
    re.I,
)


@dataclass
class DependencyCandidate:
    """An inferred dependency between two markets with an estimated arb profit."""

    market_a: MarketInfo   # broader / parent (must have higher price)
    market_b: MarketInfo   # narrower / child (must have lower price)
    dep_type: DependencyType
    price_a: float
    price_b: float
    violation: float       # price_b - price_a  (positive = arb exists)
    expected_profit: float

    @property
    def arb_exists(self) -> bool:
        """True if prices are logically inconsistent (violation > threshold)."""
        return self.violation > MIN_PROFIT_THRESHOLD


class DependencyDetector:
    """Detect logical price inconsistencies between related Polymarket markets.

    Args:
        gamma: :class:`~scanner.gamma_client.GammaClient` instance.
        min_profit: Minimum USDC profit per share to report.
        use_lp: Whether to apply scipy LP for global consistency check.
    """

    def __init__(
        self,
        gamma: GammaClient,
        *,
        min_profit: float = MIN_PROFIT_THRESHOLD,
        use_lp: bool = True,
    ) -> None:
        self._gamma = gamma
        self._min_profit = min_profit
        self._use_lp = use_lp and _SCIPY_AVAILABLE

    async def scan(
        self,
        markets: Optional[list[MarketInfo]] = None,
    ) -> list[DependencyPair]:
        """Run a full dependency scan across all active markets.

        Args:
            markets: Pre-fetched market list.  If ``None``, fetches from Gamma.

        Returns:
            List of :class:`~db.models.DependencyPair` objects sorted by
            descending expected profit.
        """
        if markets is None:
            markets = await self._gamma.get_all_active_markets()

        log.info("Dependency scan: %d markets", len(markets))

        # Group markets by primary tags / category
        groups = _group_markets(markets)

        candidates: list[DependencyCandidate] = []
        for group_key, group_markets in groups.items():
            if len(group_markets) < 2:
                continue
            log.debug("Checking group '%s' (%d markets)", group_key, len(group_markets))
            group_candidates = _check_group(group_markets)
            candidates.extend(group_candidates)

        # Optional LP consistency check across each group
        if self._use_lp:
            for group_key, group_markets in groups.items():
                lp_candidates = _lp_consistency_check(group_markets, self._min_profit)
                candidates.extend(lp_candidates)

        # Deduplicate by (market_a_id, market_b_id) — keep highest profit
        seen: dict[tuple[str, str], DependencyCandidate] = {}
        for c in candidates:
            key = (c.market_a.condition_id, c.market_b.condition_id)
            if key not in seen or c.expected_profit > seen[key].expected_profit:
                seen[key] = c

        results = [c for c in seen.values() if c.expected_profit >= self._min_profit]
        results.sort(key=lambda c: c.expected_profit, reverse=True)

        log.info("Found %d dependency arb opportunities", len(results))
        return [_candidate_to_model(c) for c in results]

    async def scan_by_tags(
        self,
        tags: list[str],
        markets: Optional[list[MarketInfo]] = None,
    ) -> list[DependencyPair]:
        """Scan only markets matching specific tags.

        Args:
            tags: Tag strings to filter on.
            markets: Optional pre-fetched market list.

        Returns:
            Matching :class:`~db.models.DependencyPair` objects.
        """
        if markets is None:
            markets = await self._gamma.get_all_active_markets()

        tags_lower = {t.lower() for t in tags}
        filtered = [
            m for m in markets
            if tags_lower.intersection(t.lower() for t in m.tags)
        ]
        return await self.scan(filtered)


# ------------------------------------------------------------------
# Grouping helpers
# ------------------------------------------------------------------


def _group_markets(markets: list[MarketInfo]) -> dict[str, list[MarketInfo]]:
    """Partition markets into groups likely to have dependencies.

    Groups by (category, primary_tag).  Falls back to category alone.
    """
    groups: dict[str, list[MarketInfo]] = {}
    for m in markets:
        primary_tag = m.tags[0].lower() if m.tags else ""
        category = (m.category or "other").lower()
        key = f"{category}::{primary_tag}" if primary_tag else category
        groups.setdefault(key, []).append(m)
    return groups


# ------------------------------------------------------------------
# Rule-based pairwise checks
# ------------------------------------------------------------------


def _check_group(markets: list[MarketInfo]) -> list[DependencyCandidate]:
    """Apply rule-based dependency checks within a group."""
    candidates: list[DependencyCandidate] = []
    for i, ma in enumerate(markets):
        for mb in markets[i + 1:]:
            price_a = _best_price(ma)
            price_b = _best_price(mb)
            if price_a is None or price_b is None:
                continue

            # Check subset relationship
            dep_type = _infer_dependency(ma, mb)
            if dep_type is None:
                continue

            # For SUBSET: mb ⊆ ma → P(mb) ≤ P(ma)
            if dep_type in (DependencyType.SUBSET, DependencyType.STATE_NATIONAL,
                            DependencyType.TEAM_TOURNAMENT):
                # ma = broader, mb = narrower
                if price_b > price_a + MIN_PROFIT_THRESHOLD:
                    # Arb: buy ma (cheaper), short mb (sell NO of mb)
                    profit = price_b - price_a
                    candidates.append(DependencyCandidate(
                        market_a=ma,
                        market_b=mb,
                        dep_type=dep_type,
                        price_a=price_a,
                        price_b=price_b,
                        violation=profit,
                        expected_profit=profit,
                    ))
                elif price_a > price_b + MIN_PROFIT_THRESHOLD:
                    # Check the reverse — maybe mb was misidentified as child
                    profit = price_a - price_b
                    candidates.append(DependencyCandidate(
                        market_a=mb,
                        market_b=ma,
                        dep_type=dep_type,
                        price_a=price_b,
                        price_b=price_a,
                        violation=profit,
                        expected_profit=profit,
                    ))

            elif dep_type == DependencyType.MUTUAL_EXCLUSIVE:
                # P(A) + P(B) > 1 is impossible if mutually exclusive
                combined = price_a + price_b
                if combined > 1.0 + MIN_PROFIT_THRESHOLD:
                    profit = combined - 1.0
                    # Buy NO of both: (1 - price_a) + (1 - price_b) < 1 guarantees profit
                    candidates.append(DependencyCandidate(
                        market_a=ma,
                        market_b=mb,
                        dep_type=dep_type,
                        price_a=price_a,
                        price_b=price_b,
                        violation=profit,
                        expected_profit=profit,
                    ))

    return candidates


def _infer_dependency(ma: MarketInfo, mb: MarketInfo) -> Optional[DependencyType]:
    """Infer the dependency type between two markets, or None if none found."""
    qa, qb = ma.question, mb.question

    # State vs national election patterns
    has_state_a = bool(_STATE_RE.search(qa))
    has_state_b = bool(_STATE_RE.search(qb))
    if has_state_a != has_state_b:
        return DependencyType.STATE_NATIONAL

    # Subset patterns: "B implies A"
    for broad_re, narrow_re in _SUBSET_PATTERNS:
        if broad_re.search(qa) and narrow_re.search(qb):
            return DependencyType.SUBSET
        if broad_re.search(qb) and narrow_re.search(qa):
            return DependencyType.SUBSET

    # Team tournament
    if re.search(r"\badvances?\b|\bqualif", qa, re.I) and re.search(
        r"\bchampionship\b|\bwins?\b", qb, re.I
    ):
        return DependencyType.TEAM_TOURNAMENT
    if re.search(r"\badvances?\b|\bqualif", qb, re.I) and re.search(
        r"\bchampionship\b|\bwins?\b", qa, re.I
    ):
        return DependencyType.TEAM_TOURNAMENT

    # Mutual exclusivity: both mention the same candidate/team winning but different events
    if re.search(r"\bwins?\b", qa, re.I) and re.search(r"\bwins?\b", qb, re.I):
        # Simple heuristic: if they share significant noun phrases they might be ME
        # (Only flag if combined probability is > 1)
        return DependencyType.MUTUAL_EXCLUSIVE

    return None


def _best_price(market: MarketInfo) -> Optional[float]:
    """Return the YES token price from Gamma token list, or None."""
    for token in market.tokens:
        if token.outcome.lower() == "yes" and token.price is not None:
            return token.price
    return None


# ------------------------------------------------------------------
# LP consistency check (scipy)
# ------------------------------------------------------------------


def _lp_consistency_check(
    markets: list[MarketInfo], min_profit: float
) -> list[DependencyCandidate]:
    """Use scipy linprog to find globally inconsistent price configurations.

    For each pair (A, B) where B ⊆ A is plausible, we solve:
        Minimise  P(A) - P(B)
        s.t.      P(A) ≥ P(B)  (logical consistency)
                  0 ≤ P(i) ≤ 1
    If the minimum is < -min_profit, the observed prices violate the constraint.
    """
    if not _SCIPY_AVAILABLE or len(markets) < 2:
        return []

    candidates: list[DependencyCandidate] = []
    prices = {m.condition_id: _best_price(m) for m in markets}

    for i, ma in enumerate(markets):
        for mb in markets[i + 1:]:
            pa = prices.get(ma.condition_id)
            pb = prices.get(mb.condition_id)
            if pa is None or pb is None:
                continue

            # Only check pairs that look like subset relationships
            dep_type = _infer_dependency(ma, mb)
            if dep_type not in (DependencyType.SUBSET, DependencyType.STATE_NATIONAL,
                                DependencyType.TEAM_TOURNAMENT):
                continue

            # LP: minimise  pa_var - pb_var
            # variables = [pa_var, pb_var]
            # constraint: pa_var - pb_var >= 0  ↔  -pa_var + pb_var <= 0
            c = [-1.0, 1.0]          # minimise pa - pb (want pa - pb to be positive)
            A_ub = [[-1.0, 1.0]]     # -pa + pb <= 0
            b_ub = [0.0]
            bounds = [(max(pa - 0.05, 0.0), min(pa + 0.05, 1.0)),
                      (max(pb - 0.05, 0.0), min(pb + 0.05, 1.0))]

            try:
                result = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
            except Exception as exc:  # noqa: BLE001
                log.debug("LP failed for pair (%s, %s): %s", ma.condition_id[:8], mb.condition_id[:8], exc)
                continue

            if result.success and result.fun < -min_profit:
                # The LP found prices satisfying the constraint with pa > pb,
                # but actual prices have pb > pa — violation detected
                if pb > pa + min_profit:
                    profit = pb - pa
                    candidates.append(DependencyCandidate(
                        market_a=ma,
                        market_b=mb,
                        dep_type=dep_type,
                        price_a=pa,
                        price_b=pb,
                        violation=profit,
                        expected_profit=profit,
                    ))

    return candidates


# ------------------------------------------------------------------
# Conversion
# ------------------------------------------------------------------


def _candidate_to_model(c: DependencyCandidate) -> DependencyPair:
    """Convert a :class:`DependencyCandidate` to a :class:`DependencyPair` model."""
    return DependencyPair(
        market_a_id=c.market_a.condition_id,
        market_b_id=c.market_b.condition_id,
        dependency_type=c.dep_type,
        price_a=c.price_a,
        price_b=c.price_b,
        expected_profit=c.expected_profit,
    )
