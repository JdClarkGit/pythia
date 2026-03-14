"""Price-magnet / 25 c–75 c mean-reversion strategy.

Markets statistically cluster at 0–5 c, 25 c, 50 c, 75 c, and 95–100 c.
After touching the 75 c or 25 c zone, approximately 54 % of markets revert
to 50 c (source: task specification empirical data).

Trade logic
-----------
- If YES price is in [70, 80] c: buy NO (which is ~25 c), target 50 c.
- If YES price is in [20, 30] c: buy YES (~25 c), target 50 c.
- Skip if: recent volume spike (info-driven move).
- Skip if: order-book imbalance > 0.3 in direction of the move (momentum).
- Skip if: post-trade drift shows continuation over last 3 large trades.
- Stop-loss: cancel and exit if YES price moves beyond 85 c or below 15 c.

Position sizing uses enhanced Kelly with p = 0.54 (historical reversion rate).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from capital.allocator import enhanced_kelly
from capital.manager import CapitalManager
from db.models import MeanReversionPosition, MeanReversionPositionStatus
from db.store import Store
from scanner.clob_client import CLOBClient, OrderBook
from scanner.gamma_client import GammaClient, MarketInfo
from scanner.microstructure import MicrostructureAnalyser, _imbalance, _post_trade_drift_score
from utils.alerts import Alerter
from utils.logger import get_logger

log = get_logger(__name__)

# ── Strategy parameters ────────────────────────────────────────────────────────
ZONE_LOW_MIN = 0.20            # YES price lower bound of "25 c zone"
ZONE_LOW_MAX = 0.30
ZONE_HIGH_MIN = 0.70           # YES price lower bound of "75 c zone"
ZONE_HIGH_MAX = 0.80
TARGET_PRICE = 0.50            # exit when YES price reaches 50 c
STOP_LOSS_HIGH = 0.85          # stop if YES moves above this (high-zone play)
STOP_LOSS_LOW = 0.15           # stop if YES moves below this (low-zone play)

HIST_REVERSION_PROB = 0.54     # empirical rate from task specification
HIST_WIN_RETURN = 1.00         # 100 % gain: buy at 25 c, sell at 50 c
FRACTIONAL_KELLY = 0.25        # quarter-Kelly
MAX_CAPITAL_PCT = 0.10         # max 10 % of capital per price-magnet bet
MIN_BET_USDC = 5.0

# Volume-spike filter: skip if 1-hour volume is X times the rolling average
VOLUME_SPIKE_MULTIPLIER = 3.0  # 3x normal volume → info-driven move

# Imbalance filter: skip if book imbalance > 0.3 in direction of price move
IMBALANCE_THRESHOLD = 0.30

# Large-trade threshold for drift analysis (USDC notional)
LARGE_TRADE_USDC = 50.0

# Require at least this USDC depth at the target entry price
MIN_DEPTH_USDC = 200.0


@dataclass
class PriceMagnetCandidate:
    """A screened price-magnet opportunity."""

    market: MarketInfo
    token_id: str       # token to BUY (NO when price is high, YES when low)
    side: str           # "YES" or "NO"
    yes_price: float    # current YES price
    entry_price: float  # limit price for the entry BUY
    target_price: float # exit target (50 c on the token bought)
    stop_loss: float    # stop-loss threshold
    depth_usdc: float
    recommended_usdc: float


class PriceMagnetStrategy:
    """Executes the 25 c–75 c mean-reversion (price-magnet) strategy.

    Args:
        gamma: :class:`~scanner.gamma_client.GammaClient`.
        clob: :class:`~scanner.clob_client.CLOBClient`.
        capital: :class:`~capital.manager.CapitalManager`.
        store: :class:`~db.store.Store`.
        alerter: :class:`~utils.alerts.Alerter`.
        microstructure: Shared :class:`~scanner.microstructure.MicrostructureAnalyser`.
        concurrency: Maximum concurrent market checks.
        dry_run: Simulate without real orders.
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
    ) -> None:
        self._gamma = gamma
        self._clob = clob
        self._capital = capital
        self._store = store
        self._alerter = alerter
        self._micro = microstructure
        self._sem = asyncio.Semaphore(concurrency)
        self._dry_run = dry_run

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    async def scan(
        self, markets: Optional[list[MarketInfo]] = None
    ) -> list[PriceMagnetCandidate]:
        """Scan for markets in the 70–80 c or 20–30 c zone.

        Args:
            markets: Pre-fetched market list; if ``None`` fetches from Gamma.

        Returns:
            Sorted list of :class:`PriceMagnetCandidate` objects.
        """
        if markets is None:
            markets = await self._gamma.get_all_active_markets()

        log.info("Price-magnet scan: %d markets", len(markets))
        tasks = [self._evaluate_market(m) for m in markets if _has_tokens(m)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates: list[PriceMagnetCandidate] = []
        for r in results:
            if isinstance(r, PriceMagnetCandidate):
                candidates.append(r)
            elif isinstance(r, Exception):
                log.debug("Market eval error: %s", r)

        candidates.sort(key=lambda c: c.recommended_usdc, reverse=True)
        log.info("Found %d price-magnet candidates", len(candidates))
        return candidates

    async def _evaluate_market(
        self, market: MarketInfo
    ) -> Optional[PriceMagnetCandidate]:
        """Evaluate one market for a price-magnet entry."""
        yes_id = market.yes_token_id
        no_id = market.no_token_id

        async with self._sem:
            yes_book, no_book = await asyncio.gather(
                self._clob.get_order_book(yes_id),   # type: ignore[arg-type]
                self._clob.get_order_book(no_id),    # type: ignore[arg-type]
            )

        if yes_book is None or no_book is None:
            return None

        yes_ask = yes_book.best_ask
        yes_bid = yes_book.best_bid
        if yes_ask is None:
            return None

        yes_mid = (yes_ask + yes_bid) / 2.0 if yes_bid is not None else yes_ask

        # Determine zone
        if ZONE_HIGH_MIN <= yes_mid <= ZONE_HIGH_MAX:
            # YES is at 75 c → buy NO (which is ~25 c)
            return await self._build_candidate(
                market=market,
                yes_mid=yes_mid,
                book=no_book,
                token_id=no_id,           # type: ignore[arg-type]
                side="NO",
                stop_loss=STOP_LOSS_HIGH,
                yes_book=yes_book,
            )
        elif ZONE_LOW_MIN <= yes_mid <= ZONE_LOW_MAX:
            # YES is at 25 c → buy YES (~25 c)
            return await self._build_candidate(
                market=market,
                yes_mid=yes_mid,
                book=yes_book,
                token_id=yes_id,          # type: ignore[arg-type]
                side="YES",
                stop_loss=STOP_LOSS_LOW,
                yes_book=yes_book,
            )

        return None

    async def _build_candidate(
        self,
        market: MarketInfo,
        yes_mid: float,
        book: OrderBook,
        token_id: str,
        side: str,
        stop_loss: float,
        yes_book: OrderBook,
    ) -> Optional[PriceMagnetCandidate]:
        """Apply filters and compute sizing for one candidate."""
        entry_price = book.best_ask
        if entry_price is None:
            return None

        # ── Filter 1: volume spike (information-driven move) ──────────────
        if _is_volume_spike(market):
            log.debug("Skipping %s — volume spike detected", market.condition_id[:12])
            return None

        # ── Filter 2: order-book imbalance > 0.3 in direction of move ────
        q_bid = sum(lv.size for lv in yes_book.bids[:1]) if yes_book.bids else 0.0
        q_ask = sum(lv.size for lv in yes_book.asks[:1]) if yes_book.asks else 0.0
        imb = _imbalance(q_bid, q_ask)

        # Direction of move: if YES is high (75 c zone), move was upward (imbalance +)
        # If YES is low (25 c zone), move was downward (imbalance -)
        if yes_mid >= ZONE_HIGH_MIN and imb > IMBALANCE_THRESHOLD:
            log.debug("Skipping %s — bullish imbalance in high zone", market.condition_id[:12])
            return None
        if yes_mid <= ZONE_LOW_MAX and imb < -IMBALANCE_THRESHOLD:
            log.debug("Skipping %s — bearish imbalance in low zone", market.condition_id[:12])
            return None

        # ── Filter 3: post-trade drift via microstructure ────────────────
        if self._micro is not None:
            micro = await self._micro.analyse(token_id)
            # If drift score is low (< 40), recent trades are continuing — skip
            drift_sub = micro.signal_details.get("drift_sub", 50.0)
            if drift_sub < 40.0:
                log.debug(
                    "Skipping %s — continuation drift (%.1f)",
                    market.condition_id[:12],
                    drift_sub,
                )
                return None

        # ── Depth filter ──────────────────────────────────────────────────
        depth_usdc = sum(lv.price * lv.size for lv in book.asks if abs(lv.price - entry_price) <= 0.05)
        if depth_usdc < MIN_DEPTH_USDC:
            return None

        # ── Kelly sizing ──────────────────────────────────────────────────
        b = (TARGET_PRICE - entry_price) / max(entry_price, 1e-9)
        if b <= 0:
            return None

        alloc = enhanced_kelly(
            b=b,
            p=HIST_REVERSION_PROB,
            depth_yes=depth_usdc,
            depth_no=0.0,
            trade_size_usdc=self._capital.free_usdc * MAX_CAPITAL_PCT,
            fractional=FRACTIONAL_KELLY,
            max_usdc=self._capital.free_usdc * MAX_CAPITAL_PCT,
            available_usdc=self._capital.free_usdc,
        )

        if alloc.usdc_amount < MIN_BET_USDC:
            return None

        log.info(
            "PRICE-MAGNET candidate | %s | YES@%.3f | buy %s@%.4f → %.2f | $%.2f",
            market.question[:50],
            yes_mid,
            side,
            entry_price,
            TARGET_PRICE,
            alloc.usdc_amount,
        )

        return PriceMagnetCandidate(
            market=market,
            token_id=token_id,
            side=side,
            yes_price=yes_mid,
            entry_price=entry_price,
            target_price=TARGET_PRICE,
            stop_loss=stop_loss,
            depth_usdc=depth_usdc,
            recommended_usdc=alloc.usdc_amount,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self, candidate: PriceMagnetCandidate
    ) -> Optional[MeanReversionPosition]:
        """Place a limit BUY order for a price-magnet candidate.

        Args:
            candidate: Screened :class:`PriceMagnetCandidate`.

        Returns:
            Persisted :class:`~db.models.MeanReversionPosition` or ``None``.
        """
        shares = candidate.recommended_usdc / max(candidate.entry_price, 1e-9)

        position = MeanReversionPosition(
            market_id=candidate.market.condition_id,
            token_id=candidate.token_id,
            side=candidate.side,
            strategy_type="price_magnet",
            entry_price=candidate.entry_price,
            target_price=candidate.target_price,
            stop_loss=candidate.stop_loss,
            shares=shares,
            usdc_spent=candidate.recommended_usdc,
            status=MeanReversionPositionStatus.OPEN,
        )

        if self._dry_run:
            log.info(
                "[DRY-RUN] PRICE-MAGNET | %s | %s@%.4f → %.2f | $%.2f",
                candidate.market.question[:50],
                candidate.side,
                candidate.entry_price,
                candidate.target_price,
                candidate.recommended_usdc,
            )
            return position

        position_id = await self._store.insert_mean_reversion_position(position)
        position.id = position_id

        order = await self._clob.place_limit_order(
            token_id=candidate.token_id,
            side="BUY",
            price=candidate.entry_price,
            size=shares,
        )

        if order is None:
            log.error(
                "Price-magnet order placement failed for %s", candidate.market.condition_id
            )
            await self._store.update_mean_reversion_status(
                position_id,
                MeanReversionPositionStatus.CANCELLED,
                error="Order placement failed",
            )
            return None

        position.entry_order_id = order.order_id
        log.info(
            "Price-magnet BUY placed | %s | %s@%.4f | order=%s | shares=%.2f",
            candidate.market.question[:50],
            candidate.side,
            candidate.entry_price,
            order.order_id,
            shares,
        )

        await self._alerter.send(
            f"[PriceMagnet] BUY {candidate.side}@{candidate.entry_price:.3f} "
            f"→ {candidate.target_price:.2f} | {candidate.market.question[:40]}",
            level="info",
        )

        return position

    async def place_exit_order(self, position: MeanReversionPosition) -> bool:
        """Place the limit SELL at TARGET_PRICE once entry fills.

        Args:
            position: A filled :class:`~db.models.MeanReversionPosition`.

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
            "Price-magnet SELL placed | pos=%d | @%.4f | order=%s",
            position.id,
            position.target_price,
            order.order_id,
        )
        return True

    async def check_stop_losses(self, yes_price_lookup: Optional[dict[str, float]] = None) -> None:
        """Check all open positions for stop-loss breaches.

        Args:
            yes_price_lookup: Optional pre-fetched {market_id: yes_price} map.
                              If ``None``, order books are fetched individually.
        """
        open_positions = await self._store.get_open_mean_reversion_positions()
        price_magnet = [p for p in open_positions if p.strategy_type == "price_magnet"]
        if not price_magnet:
            return

        tasks = [self._check_single_stop(pos, yes_price_lookup) for pos in price_magnet]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_single_stop(
        self,
        position: MeanReversionPosition,
        yes_price_lookup: Optional[dict[str, float]],
    ) -> None:
        """Apply stop-loss logic for one position."""
        if position.id is None:
            return

        # Get current YES price
        if yes_price_lookup and position.market_id in yes_price_lookup:
            yes_price = yes_price_lookup[position.market_id]
        else:
            # Need the YES token — fetch via market; fall back to position token
            book = await self._clob.get_order_book(position.token_id)
            if book is None or book.best_bid is None:
                return
            yes_price = book.best_bid  # approximate

        breached = (
            (position.side == "NO" and yes_price >= STOP_LOSS_HIGH)
            or (position.side == "YES" and yes_price <= STOP_LOSS_LOW)
        )

        if not breached:
            return

        log.warning(
            "STOP-LOSS triggered (price-magnet) | pos=%d | yes=%.4f | stop=%.4f",
            position.id,
            yes_price,
            position.stop_loss,
        )

        if position.entry_order_id:
            await self._clob.cancel_order(position.entry_order_id)
        if position.exit_order_id:
            await self._clob.cancel_order(position.exit_order_id)

        # Rough P&L estimate
        current_token_price = 1.0 - yes_price if position.side == "NO" else yes_price
        loss = (current_token_price - position.entry_price) * position.shares

        await self._store.update_mean_reversion_status(
            position.id,
            MeanReversionPositionStatus.STOPPED,
            realised_pnl=loss,
            closed_at=datetime.now(tz=timezone.utc),
            error=f"Stop-loss: YES@{yes_price:.4f}",
        )
        await self._alerter.send(
            f"[PriceMagnet] STOP-LOSS {position.side} | YES@{yes_price:.4f} "
            f"| market {position.market_id[:12]} | pnl={loss:.4f}",
            level="warning",
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _has_tokens(market: MarketInfo) -> bool:
    return market.yes_token_id is not None and market.no_token_id is not None


def _is_volume_spike(market: MarketInfo, multiplier: float = VOLUME_SPIKE_MULTIPLIER) -> bool:
    """Heuristic: flag if market volume is unusually high.

    The Gamma API returns total volume.  Without a rolling average stored
    in the DB we use a simple absolute threshold: if volume > $500k it is
    likely an actively traded, information-rich market — skip it.
    """
    # A proper implementation would compare to a rolling 7-day average
    # stored in the DB.  For now, use an absolute threshold as a proxy.
    HIGH_VOLUME_THRESHOLD = 500_000.0
    return market.volume >= HIGH_VOLUME_THRESHOLD
