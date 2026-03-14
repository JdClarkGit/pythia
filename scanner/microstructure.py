"""Order-book microstructure signal calculator.

Computes seven signals from the CLOB order book and recent trade history,
combines them into a composite score (0–100), and recommends whether to
enter or skip a mean-reversion trade.

Signals:
    1. Signed Trade Flow  — directional pressure from recent fills
    2. Relative Trade Size — normalise trade size against available liquidity
    3. Spread Regime       — wide spread ↔ temporary distortion vs tight ↔ info move
    4. Order Book Imbalance — (Q_bid - Q_ask) / (Q_bid + Q_ask)
    5. Post-Trade Drift    — did recent large trades revert or continue?
    6. Extremity Score     — min(p, 1-p); lower ↔ higher info content
    7. VWAP               — realistic expected fill price across book depth
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scanner.clob_client import CLOBClient, OrderBook, PriceLevel
from utils.logger import get_logger

log = get_logger(__name__)

CLOB_BASE = "https://clob.polymarket.com"

# Weights for the composite score (must sum to 100)
_W_FLOW = 20        # signed trade flow
_W_SPREAD = 15      # spread regime
_W_IMBALANCE = 20   # order book imbalance
_W_DRIFT = 25       # post-trade drift (most predictive for reversion)
_W_EXTREMITY = 10   # extremity score
_W_RTS = 10         # relative trade size


@dataclass
class TradeRecord:
    """A single historical trade from the CLOB /trades endpoint."""

    timestamp: float          # unix epoch seconds
    side: str                 # "BUY" or "SELL"
    price: float
    size: float               # number of tokens


@dataclass
class MicrostructureScore:
    """Result of the microstructure analysis for one token.

    Attributes:
        token_id: Token analysed.
        score: Composite score 0–100.  > 60 → OK to enter reversion.
        imbalance: Raw imbalance signal [-1, +1].
        spread: Raw bid-ask spread.
        net_flow: Signed net trade flow over the look-back window.
        vwap: Estimated VWAP fill price for a standard size order.
        signal_details: Dict with per-signal sub-scores.
        reversion_favoured: ``True`` when score > 60 and spread/drift
                            indicate a temporary distortion, not
                            an information-driven move.
    """

    token_id: str
    score: float
    imbalance: float
    spread: float
    net_flow: float
    vwap: float
    signal_details: dict[str, float] = field(default_factory=dict)
    reversion_favoured: bool = False


class MicrostructureAnalyser:
    """Computes microstructure signals for a CLOB token.

    Args:
        clob: Authenticated :class:`~scanner.clob_client.CLOBClient`.
        base_url: Override CLOB base URL (tests).
        trade_window_secs: Look-back window for trade history (seconds).
        large_trade_threshold: Minimum USDC notional to count as a "large" trade
                               for drift analysis.
        order_size_usdc: Reference order size for VWAP and p_execution estimation.
        score_threshold: Minimum score required to flag reversion as favoured.
    """

    def __init__(
        self,
        clob: CLOBClient,
        base_url: str = CLOB_BASE,
        *,
        trade_window_secs: int = 900,   # 15 minutes
        large_trade_threshold: float = 100.0,
        order_size_usdc: float = 50.0,
        score_threshold: float = 60.0,
    ) -> None:
        self._clob = clob
        self._base = base_url.rstrip("/")
        self._window = trade_window_secs
        self._large_thresh = large_trade_threshold
        self._order_size = order_size_usdc
        self._threshold = score_threshold
        self._http: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "MicrostructureAnalyser":
        self._http = httpx.AsyncClient(
            base_url=self._base,
            timeout=10.0,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyse(self, token_id: str) -> MicrostructureScore:
        """Run all 7 signals and return a composite score.

        Args:
            token_id: ERC-1155 token ID.

        Returns:
            :class:`MicrostructureScore` with all signal values.
        """
        book_task = self._clob.get_order_book(token_id)
        trades_task = self._fetch_recent_trades(token_id)

        book, trades = await asyncio.gather(book_task, trades_task)

        if book is None:
            log.debug("microstructure: no order book for %s", token_id)
            return MicrostructureScore(
                token_id=token_id, score=0.0, imbalance=0.0,
                spread=1.0, net_flow=0.0, vwap=0.0,
            )

        # --- Signal 4: Order Book Imbalance ---
        q_bid = sum(lv.size for lv in book.bids[:1]) if book.bids else 0.0
        q_ask = sum(lv.size for lv in book.asks[:1]) if book.asks else 0.0
        imbalance = _imbalance(q_bid, q_ask)

        # --- Signal 3: Spread Regime ---
        spread = _spread(book)

        # --- Signal 7: VWAP ---
        vwap = _vwap(book.asks, self._order_size)

        # --- Signal 6: Extremity ---
        mid = ((book.best_bid or 0.0) + (book.best_ask or 1.0)) / 2.0
        extremity = min(mid, 1.0 - mid)  # [0, 0.5] — lower ↔ deeper extreme

        # --- Signals 1, 2, 5 from trade history ---
        net_flow = _signed_trade_flow(trades)
        rts = _relative_trade_size(trades, q_bid, q_ask)
        drift_score = _post_trade_drift_score(trades, self._large_thresh)

        # --- Composite scoring ---
        # For mean-reversion we WANT:
        #   wide spread (distortion, not info)
        #   imbalance opposing the recent price move
        #   trades show reversion in drift
        #   extremity near 0 (deep underdog — high info per tick)
        #   moderate net_flow (not runaway momentum)

        # Spread sub-score: wide spread → high score (distortion, not info)
        spread_sub = min(spread / 0.05, 1.0) * 100.0   # 5 c spread → full score

        # Imbalance sub-score: absolute imbalance low → balanced book → reversion likely
        # (if imbalance > 0.3 in direction of move, skip)
        imbalance_sub = (1.0 - abs(imbalance)) * 100.0

        # Net-flow sub-score: low absolute flow → no momentum → reversion more likely
        flow_norm = min(abs(net_flow) / max(q_bid + q_ask, 1.0), 1.0)
        flow_sub = (1.0 - flow_norm) * 100.0

        # Drift sub-score: comes from _post_trade_drift_score (0-100, higher = reversion)
        drift_sub = drift_score

        # Extremity sub-score: closer to 0 → higher expected reversion magnitude
        extremity_sub = (1.0 - extremity / 0.5) * 100.0  # 0 → 100, 0.5 → 0

        # RTS sub-score: small relative size → less impact → more reversion
        rts_sub = (1.0 - min(rts, 1.0)) * 100.0

        composite = (
            _W_FLOW * flow_sub / 100.0
            + _W_SPREAD * spread_sub / 100.0
            + _W_IMBALANCE * imbalance_sub / 100.0
            + _W_DRIFT * drift_sub / 100.0
            + _W_EXTREMITY * extremity_sub / 100.0
            + _W_RTS * rts_sub / 100.0
        )

        details = {
            "flow_sub": round(flow_sub, 2),
            "spread_sub": round(spread_sub, 2),
            "imbalance_sub": round(imbalance_sub, 2),
            "drift_sub": round(drift_sub, 2),
            "extremity_sub": round(extremity_sub, 2),
            "rts_sub": round(rts_sub, 2),
        }

        reversion_favoured = (
            composite >= self._threshold
            and abs(imbalance) <= 0.3   # spec: imbalance > 0.3 → skip
            and spread > 0.01           # some spread needed for reversion play
        )

        log.debug(
            "microstructure %s: score=%.1f imbalance=%.3f spread=%.4f vwap=%.4f",
            token_id[:12],
            composite,
            imbalance,
            spread,
            vwap,
        )

        return MicrostructureScore(
            token_id=token_id,
            score=round(composite, 2),
            imbalance=imbalance,
            spread=spread,
            net_flow=net_flow,
            vwap=vwap,
            signal_details=details,
            reversion_favoured=reversion_favoured,
        )

    # ------------------------------------------------------------------
    # Trade history
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=False,
    )
    async def _fetch_recent_trades(self, token_id: str) -> list[TradeRecord]:
        """Fetch recent fills from the CLOB /trades endpoint.

        Args:
            token_id: ERC-1155 token ID.

        Returns:
            List of :class:`TradeRecord` sorted newest-first.
        """
        if self._http is None:
            # Fallback: create a temporary client
            async with httpx.AsyncClient(
                base_url=self._base, timeout=10.0,
                headers={"Accept": "application/json"}, follow_redirects=True,
            ) as tmp:
                return await self._parse_trades(
                    await tmp.get("/trades", params={"token_id": token_id}),
                    token_id,
                )

        try:
            resp = await self._http.get("/trades", params={"token_id": token_id})
            resp.raise_for_status()
            return self._parse_trade_response(resp.json())
        except Exception as exc:  # noqa: BLE001
            log.debug("Failed to fetch trades for %s: %s", token_id[:12], exc)
            return []

    async def _parse_trades(self, resp: Any, token_id: str) -> list[TradeRecord]:
        try:
            resp.raise_for_status()
            return self._parse_trade_response(resp.json())
        except Exception as exc:  # noqa: BLE001
            log.debug("Trade parse error for %s: %s", token_id[:12], exc)
            return []

    def _parse_trade_response(self, data: Any) -> list[TradeRecord]:
        """Convert raw /trades JSON to :class:`TradeRecord` list."""
        now = time.time()
        cutoff = now - self._window
        records: list[TradeRecord] = []

        items = data if isinstance(data, list) else data.get("data", data.get("trades", []))
        for item in items:
            try:
                ts = float(item.get("timestamp", item.get("created_at", 0)) or 0)
                if ts < cutoff:
                    continue
                side = str(item.get("side", item.get("takerSide", "BUY"))).upper()
                price = float(item.get("price", 0) or 0)
                size = float(item.get("size", item.get("amount", 0)) or 0)
                if price > 0 and size > 0:
                    records.append(TradeRecord(timestamp=ts, side=side, price=price, size=size))
            except (TypeError, ValueError):
                continue

        records.sort(key=lambda t: t.timestamp, reverse=True)
        return records


# ------------------------------------------------------------------
# Pure signal functions (easily testable in isolation)
# ------------------------------------------------------------------


def _imbalance(q_bid: float, q_ask: float) -> float:
    """Order Book Imbalance = (Q_bid - Q_ask) / (Q_bid + Q_ask).

    Returns value in [-1, +1].  +1 = pure buy pressure.
    """
    total = q_bid + q_ask
    if total < 1e-9:
        return 0.0
    return (q_bid - q_ask) / total


def _spread(book: OrderBook) -> float:
    """Bid-ask spread.  Returns 0 if either side is empty."""
    best_ask = book.best_ask
    best_bid = book.best_bid
    if best_ask is None or best_bid is None:
        return 0.0
    return max(best_ask - best_bid, 0.0)


def _signed_trade_flow(trades: list[TradeRecord]) -> float:
    """Net signed trade flow: sum(side_i * size_i).

    BUY = +1, SELL = -1.
    """
    flow = 0.0
    for t in trades:
        sign = 1.0 if t.side == "BUY" else -1.0
        flow += sign * t.size
    return flow


def _relative_trade_size(
    trades: list[TradeRecord], q_bid: float, q_ask: float
) -> float:
    """Relative Trade Size = recent_avg_size / (Q_bestbid + Q_bestask).

    Returns a normalised value in [0, ∞).  Values > 1 mean trades are
    consuming more than the full best-level depth on average.
    """
    total_liquidity = q_bid + q_ask
    if not trades or total_liquidity < 1e-9:
        return 0.0
    avg_size = sum(t.size for t in trades) / len(trades)
    return avg_size / total_liquidity


def _post_trade_drift_score(
    trades: list[TradeRecord], large_trade_usdc: float
) -> float:
    """Score the post-trade drift of the last 3 large trades.

    If after a BUY trade the price subsequently fell (mean reversion),
    or after a SELL trade the price rose, assign a high reversion score.
    If continuation, assign 0.

    Returns:
        Score in [0, 100]: 100 = clear reversion, 0 = clear continuation.
    """
    large = [t for t in trades if t.price * t.size >= large_trade_usdc]
    if len(large) < 2:
        return 50.0  # insufficient data — neutral

    # Look at pairs of consecutive large trades
    reversion_count = 0
    continuation_count = 0
    pairs_checked = 0

    for i in range(min(len(large) - 1, 3)):
        prev = large[i + 1]   # older trade (list is newest-first)
        curr = large[i]       # newer trade

        price_change = curr.price - prev.price
        if prev.side == "BUY" and price_change < 0:
            reversion_count += 1
        elif prev.side == "SELL" and price_change > 0:
            reversion_count += 1
        elif abs(price_change) > 1e-6:
            continuation_count += 1
        pairs_checked += 1

    if pairs_checked == 0:
        return 50.0

    reversion_ratio = reversion_count / pairs_checked
    return reversion_ratio * 100.0


def _vwap(levels: list[PriceLevel], order_size_usdc: float) -> float:
    """Volume-Weighted Average Price across book depth for a given order size.

    Sweeps the ask side until ``order_size_usdc`` USDC worth of tokens
    have been filled.

    Args:
        levels: Ask price levels (sorted ascending by price).
        order_size_usdc: Desired USDC notional to fill.

    Returns:
        Estimated average fill price per token.  Returns best ask if
        the book has insufficient depth.
    """
    if not levels:
        return 0.0

    remaining_usdc = order_size_usdc
    total_tokens = 0.0
    total_usdc = 0.0

    for lv in levels:
        usdc_at_level = lv.price * lv.size
        if remaining_usdc <= usdc_at_level:
            tokens_here = remaining_usdc / lv.price
            total_tokens += tokens_here
            total_usdc += remaining_usdc
            remaining_usdc = 0.0
            break
        total_tokens += lv.size
        total_usdc += usdc_at_level
        remaining_usdc -= usdc_at_level

    if total_tokens < 1e-9:
        return levels[0].price

    return total_usdc / total_tokens
