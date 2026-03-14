"""Mean-reversion strategy at price extremes (deep underdogs at 1–15 c).

Logic
-----
- Scan for YES or NO tokens priced between $0.03 and $0.15.
- Filter: market must have > 30 days to resolution.
- Filter: order-book depth ≥ $500 at the entry price.
- Place a maker limit BUY at the current best ask (or slightly below).
- Post a maker limit SELL at $0.87–$0.93 once the entry is filled.
- Stop-loss: cancel and exit at $0.005.
- Never commit more than 5 % of available capital to a single position.

Position sizing uses execution-risk-adjusted Kelly:
    f = (b * p - q) / b * sqrt(p)
where
    b = (target_price - entry_price) / entry_price   (profit multiple)
    p = historical success probability (by entry-price bucket)
    q = 1 - p
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from capital.allocator import enhanced_kelly
from capital.manager import CapitalManager
from db.models import MeanReversionPosition, MeanReversionPositionStatus
from db.store import Store
from scanner.clob_client import CLOBClient, OrderBook
from scanner.gamma_client import GammaClient, MarketInfo
from scanner.microstructure import MicrostructureAnalyser
from utils.alerts import Alerter
from utils.logger import get_logger

log = get_logger(__name__)

# ── Strategy parameters ────────────────────────────────────────────────────────
ENTRY_PRICE_MIN = 0.03        # never buy below 3 c (too illiquid)
ENTRY_PRICE_MAX = 0.15        # only look at tokens ≤ 15 c
TARGET_SELL_PRICE = 0.90      # default exit target
STOP_LOSS_PRICE = 0.005       # exit if price drops to 0.5 c
MIN_DAYS_TO_RESOLUTION = 30   # skip markets resolving in < 30 days
MIN_DEPTH_USDC = 500.0        # minimum book depth at entry price
MAX_CAPITAL_PCT = 0.05        # hard cap: 5 % of free capital per position
FRACTIONAL_KELLY = 0.25       # conservative quarter-Kelly
MIN_BET_USDC = 5.0            # ignore tiny allocations (dust)
MICROSTRUCTURE_THRESHOLD = 60.0  # minimum microstructure score to enter

# Historical outcome distribution by entry-price bucket.
# Each entry: (price_ceiling, p_90c, p_50c)
# p_90c  = probability of reaching 90 c+ (800 % return at 10 c entry)
# p_50c  = probability of reaching 50 c+ but < 90 c (400 % return at 10 c entry)
# Sources: task specification empirical data.
_HIST_OUTCOMES: list[tuple[float, float, float]] = [
    (0.05, 0.03, 0.03),   # 0–5 c:  combined p ≈ 6 % (EV slightly negative — skip)
    (0.10, 0.06, 0.13),   # 5–10 c: combined p = 19 % (EV positive)
    (0.15, 0.10, 0.13),   # 10–15 c: combined p = 23 % (EV positive)
]


def _hist_kelly_params(entry_price: float) -> tuple[float, float]:
    """Return (combined_p, weighted_b) for the Kelly formula.

    Uses the full multi-outcome distribution so that ``b * p - q > 0``
    correctly reflects the strategy's positive expected value.

    At a 10 c entry the two positive outcomes are:
      - 6 % chance → 90 c exit: b = (90 - 10) / 10 = 8.0
      - 13 % chance → 50 c exit: b = (50 - 10) / 10 = 4.0
    Combined p = 0.19, weighted b = (0.06*8 + 0.13*4) / 0.19 ≈ 5.26
    Kelly = (5.26 * 0.19 - 0.81) / 5.26 ≈ 0.036 → positive
    """
    for ceiling, p_high, p_mid in _HIST_OUTCOMES:
        if entry_price <= ceiling:
            p_combined = p_high + p_mid
            if p_combined < 1e-9:
                return 0.0, 0.0
            # Profit multiples relative to entry price
            b_high = (TARGET_SELL_PRICE - entry_price) / max(entry_price, 1e-9)
            b_mid = (0.50 - entry_price) / max(entry_price, 1e-9)
            b_mid = max(b_mid, 0.0)
            b_weighted = (p_high * b_high + p_mid * b_mid) / p_combined
            return p_combined, b_weighted
    # Fallback for prices above 0.15 (shouldn't reach here given filters)
    return 0.10, 5.0


def _hist_success_rate(entry_price: float) -> float:
    """Look up the combined historical success probability for an entry price."""
    p, _ = _hist_kelly_params(entry_price)
    return p


@dataclass
class MeanReversionCandidate:
    """A screened market ready for position sizing and order placement."""

    market: MarketInfo
    token_id: str
    side: str            # "YES" or "NO"
    entry_price: float   # limit price for the BUY
    target_price: float  # limit price for the SELL exit
    stop_loss: float
    depth_usdc: float    # USDC depth available at/near entry_price
    hist_p: float        # historical success probability
    recommended_usdc: float   # Kelly-sized bet


class MeanReversionStrategy:
    """Executes the deep-underdog mean-reversion strategy.

    Args:
        gamma: :class:`~scanner.gamma_client.GammaClient` instance.
        clob: :class:`~scanner.clob_client.CLOBClient` instance.
        capital: :class:`~capital.manager.CapitalManager` instance.
        store: :class:`~db.store.Store` for persistence.
        alerter: :class:`~utils.alerts.Alerter` for notifications.
        microstructure: Optional analyser; if None a new one is created.
        concurrency: Maximum simultaneous market checks.
        dry_run: Simulate without placing real orders.
        use_microstructure: Gate entries on microstructure score.
    """

    def __init__(
        self,
        gamma: GammaClient,
        clob: CLOBClient,
        capital: CapitalManager,
        store: Store,
        alerter: Alerter,
        *,
        microstructure: Optional[MicrostructureAnalyser] = None,
        concurrency: int = 20,
        dry_run: bool = False,
        use_microstructure: bool = True,
    ) -> None:
        self._gamma = gamma
        self._clob = clob
        self._capital = capital
        self._store = store
        self._alerter = alerter
        self._micro = microstructure
        self._sem = asyncio.Semaphore(concurrency)
        self._dry_run = dry_run
        self._use_micro = use_microstructure

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    async def scan(
        self, markets: Optional[list[MarketInfo]] = None
    ) -> list[MeanReversionCandidate]:
        """Scan all active markets for mean-reversion entry candidates.

        Args:
            markets: Pre-fetched list; if ``None`` fetches from Gamma API.

        Returns:
            Sorted list of :class:`MeanReversionCandidate` objects.
        """
        if markets is None:
            markets = await self._gamma.get_all_active_markets()

        log.info("Mean-reversion scan: %d markets", len(markets))
        tasks = [self._evaluate_market(m) for m in markets if _has_tokens(m)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates: list[MeanReversionCandidate] = []
        for r in results:
            if isinstance(r, MeanReversionCandidate):
                candidates.append(r)
            elif isinstance(r, Exception):
                log.debug("Market eval error: %s", r)

        candidates.sort(key=lambda c: c.recommended_usdc, reverse=True)
        log.info("Found %d mean-reversion candidates", len(candidates))
        return candidates

    async def _evaluate_market(
        self, market: MarketInfo
    ) -> Optional[MeanReversionCandidate]:
        """Evaluate a single market for a mean-reversion opportunity."""
        # Filter: days to resolution
        if not _has_enough_time(market, MIN_DAYS_TO_RESOLUTION):
            return None

        yes_id = market.yes_token_id
        no_id = market.no_token_id

        async with self._sem:
            yes_book, no_book = await asyncio.gather(
                self._clob.get_order_book(yes_id),   # type: ignore[arg-type]
                self._clob.get_order_book(no_id),    # type: ignore[arg-type]
            )

        for book, token_id, side in [
            (yes_book, yes_id, "YES"),
            (no_book, no_id, "NO"),
        ]:
            if book is None or token_id is None:
                continue
            candidate = self._screen_book(market, book, token_id, side)
            if candidate is not None:
                return candidate

        return None

    def _screen_book(
        self,
        market: MarketInfo,
        book: OrderBook,
        token_id: str,
        side: str,
    ) -> Optional[MeanReversionCandidate]:
        """Apply entry filters to one side of a market."""
        best_ask = book.best_ask
        if best_ask is None:
            return None
        if not (ENTRY_PRICE_MIN <= best_ask <= ENTRY_PRICE_MAX):
            return None

        # Depth filter: sum USDC at or near the entry price
        depth_usdc = _depth_near_price(book, best_ask, tolerance=0.02)
        if depth_usdc < MIN_DEPTH_USDC:
            return None

        # Kelly sizing — use combined probability across both positive exit scenarios
        hist_p, b_weighted = _hist_kelly_params(best_ask)
        if hist_p < 1e-9 or b_weighted <= 0.0:
            return None  # negative-EV bucket; skip

        alloc = enhanced_kelly(
            b=b_weighted,
            p=hist_p,
            depth_yes=depth_usdc,
            depth_no=0.0,   # single-leg bet
            trade_size_usdc=self._capital.free_usdc * MAX_CAPITAL_PCT,
            fractional=FRACTIONAL_KELLY,
            max_usdc=self._capital.free_usdc * MAX_CAPITAL_PCT,
            available_usdc=self._capital.free_usdc,
        )

        if alloc.usdc_amount < MIN_BET_USDC:
            return None

        log.info(
            "MEAN-REV candidate | %s | %s@%.4f | depth=%.0f USDC | kelly=%.4f | bet=%.2f USDC",
            market.question[:50],
            side,
            best_ask,
            depth_usdc,
            alloc.kelly_fraction,
            alloc.usdc_amount,
        )

        return MeanReversionCandidate(
            market=market,
            token_id=token_id,
            side=side,
            entry_price=best_ask,
            target_price=TARGET_SELL_PRICE,
            stop_loss=STOP_LOSS_PRICE,
            depth_usdc=depth_usdc,
            hist_p=hist_p,              # combined success probability
            recommended_usdc=alloc.usdc_amount,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self, candidate: MeanReversionCandidate
    ) -> Optional[MeanReversionPosition]:
        """Place a limit BUY order for a mean-reversion candidate.

        If microstructure scoring is enabled, the signal must score
        above the threshold before an order is placed.

        Args:
            candidate: Screened :class:`MeanReversionCandidate`.

        Returns:
            Persisted :class:`~db.models.MeanReversionPosition` or ``None``.
        """
        # Microstructure gate
        if self._use_micro and self._micro is not None:
            micro_score = await self._micro.analyse(candidate.token_id)
            if not micro_score.reversion_favoured:
                log.info(
                    "Micro gate FAILED (%.1f < %.1f) for %s — skipping",
                    micro_score.score,
                    MICROSTRUCTURE_THRESHOLD,
                    candidate.market.question[:50],
                )
                return None

        shares = candidate.recommended_usdc / max(candidate.entry_price, 1e-9)
        position = MeanReversionPosition(
            market_id=candidate.market.condition_id,
            token_id=candidate.token_id,
            side=candidate.side,
            strategy_type="extreme_reversion",
            entry_price=candidate.entry_price,
            target_price=candidate.target_price,
            stop_loss=candidate.stop_loss,
            shares=shares,
            usdc_spent=candidate.recommended_usdc,
            status=MeanReversionPositionStatus.OPEN,
        )

        if self._dry_run:
            log.info(
                "[DRY-RUN] MEAN-REV | %s | %s@%.4f | shares=%.4f | usdc=%.2f | target=%.2f",
                candidate.market.question[:50],
                candidate.side,
                candidate.entry_price,
                shares,
                candidate.recommended_usdc,
                candidate.target_price,
            )
            return position

        # Persist before placing order (idempotency)
        position_id = await self._store.insert_mean_reversion_position(position)
        position.id = position_id

        # Place maker limit BUY
        order = await self._clob.place_limit_order(
            token_id=candidate.token_id,
            side="BUY",
            price=candidate.entry_price,
            size=shares,
        )

        if order is None:
            log.error(
                "Failed to place mean-reversion BUY for %s",
                candidate.market.condition_id,
            )
            await self._store.update_mean_reversion_status(
                position_id,
                MeanReversionPositionStatus.CANCELLED,
                error="Order placement failed",
            )
            return None

        await self._store.update_mean_reversion_status(
            position_id,
            MeanReversionPositionStatus.OPEN,
            exit_order_id=None,
        )

        # Update position with order ID (reuse exit_order_id field for entry)
        position.entry_order_id = order.order_id
        log.info(
            "Mean-rev BUY placed | %s | %s@%.4f | order=%s | shares=%.2f",
            candidate.market.question[:50],
            candidate.side,
            candidate.entry_price,
            order.order_id,
            shares,
        )

        await self._alerter.send(
            f"[MeanRev] BUY {candidate.side}@{candidate.entry_price:.3f} "
            f"| {candidate.market.question[:40]} | ${candidate.recommended_usdc:.2f}",
            level="info",
        )

        return position

    async def place_exit_order(
        self, position: MeanReversionPosition
    ) -> bool:
        """Place the limit SELL exit once entry is confirmed filled.

        Args:
            position: A :class:`~db.models.MeanReversionPosition` with
                      status FILLED.

        Returns:
            ``True`` if the exit order was successfully placed.
        """
        if position.id is None:
            return False

        order = await self._clob.place_limit_order(
            token_id=position.token_id,
            side="SELL",
            price=position.target_price,
            size=position.shares,
        )

        if order is None:
            log.error("Failed to place exit SELL for position %d", position.id)
            await self._store.update_mean_reversion_status(
                position.id,
                MeanReversionPositionStatus.FILLED,
                error="Exit order placement failed",
            )
            return False

        await self._store.update_mean_reversion_status(
            position.id,
            MeanReversionPositionStatus.FILLED,
            exit_order_id=order.order_id,
        )
        log.info(
            "Mean-rev SELL placed | pos=%d | @%.4f | order=%s",
            position.id,
            position.target_price,
            order.order_id,
        )
        return True

    async def check_stop_losses(self) -> None:
        """Scan open positions and trigger stop-losses if price breached.

        Should be called periodically (e.g. every 60 seconds).
        """
        open_positions = await self._store.get_open_mean_reversion_positions()
        if not open_positions:
            return

        tasks = [self._check_single_stop(pos) for pos in open_positions]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_single_stop(self, position: MeanReversionPosition) -> None:
        """Check and trigger stop-loss for one position."""
        book = await self._clob.get_order_book(position.token_id)
        if book is None or book.best_bid is None:
            return

        current_price = book.best_bid
        if current_price <= position.stop_loss and position.id is not None:
            log.warning(
                "STOP-LOSS triggered | pos=%d | price=%.5f ≤ stop=%.5f",
                position.id,
                current_price,
                position.stop_loss,
            )
            # Cancel any open orders and record the stop
            if position.entry_order_id:
                await self._clob.cancel_order(position.entry_order_id)
            if position.exit_order_id:
                await self._clob.cancel_order(position.exit_order_id)

            loss = (current_price - position.entry_price) * position.shares
            await self._store.update_mean_reversion_status(
                position.id,
                MeanReversionPositionStatus.STOPPED,
                realised_pnl=loss,
                closed_at=datetime.now(tz=timezone.utc),
                error=f"Stop-loss at {current_price:.5f}",
            )
            await self._alerter.send(
                f"[MeanRev] STOP-LOSS {position.side}@{current_price:.4f} "
                f"| market {position.market_id[:12]} | pnl={loss:.4f}",
                level="warning",
            )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _has_tokens(market: MarketInfo) -> bool:
    return market.yes_token_id is not None and market.no_token_id is not None


def _has_enough_time(market: MarketInfo, min_days: int) -> bool:
    """Return True if the market resolves at least ``min_days`` from now."""
    if market.end_date is None:
        return True  # Unknown end date — allow through (conservative)
    now = datetime.now(tz=timezone.utc)
    end = market.end_date
    # Ensure timezone-aware comparison
    if end.tzinfo is None:
        from datetime import timezone as _tz
        end = end.replace(tzinfo=_tz.utc)
    days_left = (end - now).days
    return days_left >= min_days


def _depth_near_price(book: OrderBook, price: float, tolerance: float = 0.02) -> float:
    """Sum USDC depth across ask levels within ``tolerance`` of ``price``."""
    total = 0.0
    for lv in book.asks:
        if abs(lv.price - price) <= tolerance:
            total += lv.price * lv.size
    return total
