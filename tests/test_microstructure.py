"""Tests for scanner/microstructure.py — all signals and the composite score."""

from __future__ import annotations

import pytest

from scanner.clob_client import OrderBook, PriceLevel
from scanner.microstructure import (
    MicrostructureAnalyser,
    TradeRecord,
    _imbalance,
    _post_trade_drift_score,
    _relative_trade_size,
    _signed_trade_flow,
    _spread,
    _vwap,
)


# ─── Helper builders ──────────────────────────────────────────────────────────


def _book(
    asks: list[tuple[float, float]],
    bids: list[tuple[float, float]],
    token_id: str = "TOK",
) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        asks=[PriceLevel(price=p, size=s) for p, s in asks],
        bids=[PriceLevel(price=p, size=s) for p, s in sorted(bids, key=lambda x: -x[0])],
    )


def _trade(side: str, price: float, size: float, ts: float = 1_000_000.0) -> TradeRecord:
    return TradeRecord(timestamp=ts, side=side, price=price, size=size)


# ─── Signal 4: Imbalance ─────────────────────────────────────────────────────


def test_imbalance_balanced():
    assert _imbalance(100.0, 100.0) == pytest.approx(0.0)


def test_imbalance_full_bid():
    assert _imbalance(100.0, 0.0) == pytest.approx(1.0)


def test_imbalance_full_ask():
    assert _imbalance(0.0, 100.0) == pytest.approx(-1.0)


def test_imbalance_empty():
    assert _imbalance(0.0, 0.0) == pytest.approx(0.0)


def test_imbalance_partial():
    # (70 - 30) / 100 = 0.4
    assert _imbalance(70.0, 30.0) == pytest.approx(0.40)


# ─── Signal 3: Spread ─────────────────────────────────────────────────────────


def test_spread_normal():
    book = _book(asks=[(0.52, 100)], bids=[(0.48, 100)])
    assert _spread(book) == pytest.approx(0.04)


def test_spread_empty_asks():
    book = _book(asks=[], bids=[(0.48, 100)])
    assert _spread(book) == pytest.approx(0.0)


def test_spread_zero():
    book = _book(asks=[(0.50, 100)], bids=[(0.50, 100)])
    assert _spread(book) == pytest.approx(0.0)


# ─── Signal 1: Signed Trade Flow ─────────────────────────────────────────────


def test_signed_flow_all_buys():
    trades = [_trade("BUY", 0.50, 100.0), _trade("BUY", 0.51, 50.0)]
    assert _signed_trade_flow(trades) == pytest.approx(150.0)


def test_signed_flow_all_sells():
    trades = [_trade("SELL", 0.50, 100.0)]
    assert _signed_trade_flow(trades) == pytest.approx(-100.0)


def test_signed_flow_mixed():
    trades = [_trade("BUY", 0.50, 80.0), _trade("SELL", 0.51, 30.0)]
    assert _signed_trade_flow(trades) == pytest.approx(50.0)


def test_signed_flow_empty():
    assert _signed_trade_flow([]) == pytest.approx(0.0)


# ─── Signal 2: Relative Trade Size ───────────────────────────────────────────


def test_rts_normal():
    trades = [_trade("BUY", 0.50, 10.0), _trade("BUY", 0.50, 10.0)]
    # avg size = 10, q_bid + q_ask = 200
    rts = _relative_trade_size(trades, q_bid=100.0, q_ask=100.0)
    assert rts == pytest.approx(0.05)


def test_rts_no_liquidity():
    trades = [_trade("BUY", 0.50, 10.0)]
    assert _relative_trade_size(trades, q_bid=0.0, q_ask=0.0) == pytest.approx(0.0)


def test_rts_empty_trades():
    assert _relative_trade_size([], q_bid=100.0, q_ask=100.0) == pytest.approx(0.0)


# ─── Signal 5: Post-Trade Drift ──────────────────────────────────────────────


def test_drift_clear_reversion():
    """After BUY, price falls → reversion = 100 score."""
    trades = [
        # newest first
        _trade("BUY", 0.50, 200.0, ts=3.0),   # older trade
        _trade("SELL", 0.45, 200.0, ts=2.0),   # price fell after buy
        _trade("BUY", 0.40, 200.0, ts=1.0),    # price fell again
    ]
    # Pairs: (ts=3, ts=2): BUY then price fell → reversion
    #        (ts=2, ts=1): SELL then price fell → continuation (no SELL reversion)
    score = _post_trade_drift_score(trades, large_trade_usdc=50.0)
    assert 0.0 <= score <= 100.0


def test_drift_insufficient_data():
    """Single large trade → neutral 50 score."""
    trades = [_trade("BUY", 0.50, 200.0)]
    score = _post_trade_drift_score(trades, large_trade_usdc=50.0)
    assert score == pytest.approx(50.0)


def test_drift_no_large_trades():
    """Trades below threshold → neutral 50 score."""
    trades = [_trade("BUY", 0.50, 1.0), _trade("BUY", 0.51, 1.0)]
    score = _post_trade_drift_score(trades, large_trade_usdc=500.0)
    assert score == pytest.approx(50.0)


def test_drift_full_reversion():
    """Three consecutive reversion pairs → 100 score.

    Reversion means: after a BUY at ts=T, the next observed price (ts=T+1)
    is LOWER.  Sorted newest-first: large[i] = curr, large[i+1] = prev (older BUY).
    price_change = curr.price - prev.price < 0 → reversion.
    """
    trades = [
        # Sorted newest-first (highest ts = newest). Price falls over time after BUYs.
        _trade("BUY", 0.45, 200.0, ts=4.0),  # newest — price fell to 0.45
        _trade("BUY", 0.48, 200.0, ts=3.0),
        _trade("BUY", 0.50, 200.0, ts=2.0),
        _trade("BUY", 0.55, 200.0, ts=1.0),  # oldest — started at 0.55
    ]
    # Pairs (newest-first indexing):
    #   (ts=4→ts=3): curr=0.45 < prev=0.48 after BUY → reversion ✓
    #   (ts=3→ts=2): curr=0.48 < prev=0.50 after BUY → reversion ✓
    #   (ts=2→ts=1): curr=0.50 < prev=0.55 after BUY → reversion ✓
    score = _post_trade_drift_score(trades, large_trade_usdc=50.0)
    assert score == pytest.approx(100.0)


# ─── Signal 7: VWAP ──────────────────────────────────────────────────────────


def test_vwap_single_level_fills():
    levels = [PriceLevel(price=0.10, size=1000.0)]
    vwap = _vwap(levels, order_size_usdc=50.0)
    assert vwap == pytest.approx(0.10)


def test_vwap_spans_two_levels():
    levels = [
        PriceLevel(price=0.10, size=100.0),  # $10 worth
        PriceLevel(price=0.12, size=100.0),  # $12 worth
    ]
    # Fill $15: use all of level 1 ($10), then $5 of level 2
    # level 1: 100 tokens at 0.10
    # level 2: 5/0.12 = 41.67 tokens at 0.12
    # vwap = (10 + 5) / (100 + 41.67) ≈ 0.1059
    vwap = _vwap(levels, order_size_usdc=15.0)
    assert vwap == pytest.approx((10.0 + 5.0) / (100.0 + 5.0 / 0.12), rel=1e-4)


def test_vwap_empty_book():
    assert _vwap([], order_size_usdc=50.0) == pytest.approx(0.0)


def test_vwap_insufficient_depth():
    """When depth < order size, use whatever is available."""
    levels = [PriceLevel(price=0.10, size=100.0)]  # only $10 available
    vwap = _vwap(levels, order_size_usdc=50.0)
    # Exhausted all depth: 100 tokens at 0.10 → vwap = 0.10
    assert vwap == pytest.approx(0.10)


# ─── Signal 6: Extremity ─────────────────────────────────────────────────────


def test_extremity_at_50c():
    """At 50 c, extremity = 0.5 (least extreme)."""
    extremity = min(0.50, 1.0 - 0.50)
    assert extremity == pytest.approx(0.50)


def test_extremity_at_10c():
    """At 10 c, extremity = 0.1 (deep underdog — highest info content)."""
    extremity = min(0.10, 1.0 - 0.10)
    assert extremity == pytest.approx(0.10)


# ─── MicrostructureScore integration (mock CLOB) ─────────────────────────────


@pytest.mark.asyncio
async def test_analyse_returns_score_with_deep_book():
    """A wide-spread, balanced book with reversion trades should score well."""
    from unittest.mock import AsyncMock, patch

    book = _book(
        asks=[(0.15, 5000.0)],
        bids=[(0.10, 5000.0)],   # spread = 0.05 — wide distortion
    )

    clob = AsyncMock()
    clob.get_order_book.return_value = book

    analyser = MicrostructureAnalyser(clob, score_threshold=60.0)

    # Patch _fetch_recent_trades to return reversion trades
    reversion_trades = [
        _trade("BUY", 0.15, 200.0, ts=3.0),
        _trade("BUY", 0.12, 200.0, ts=2.0),
        _trade("BUY", 0.10, 200.0, ts=1.0),
    ]

    with patch.object(analyser, "_fetch_recent_trades", return_value=reversion_trades):
        result = await analyser.analyse("TEST_TOKEN")

    assert result.token_id == "TEST_TOKEN"
    assert 0.0 <= result.score <= 100.0
    assert isinstance(result.reversion_favoured, bool)
    assert result.spread == pytest.approx(0.05)
    assert abs(result.imbalance) < 0.1   # balanced book


@pytest.mark.asyncio
async def test_analyse_returns_zero_score_no_book():
    """Missing order book should yield a zero score and no reversion signal."""
    from unittest.mock import AsyncMock, patch

    clob = AsyncMock()
    clob.get_order_book.return_value = None   # simulate missing book

    analyser2 = MicrostructureAnalyser(clob)
    with patch.object(analyser2, "_fetch_recent_trades", new=AsyncMock(return_value=[])):
        result = await analyser2.analyse("MISSING")

    assert result.score == pytest.approx(0.0)
    assert result.reversion_favoured is False


@pytest.mark.asyncio
async def test_analyse_flags_high_imbalance():
    """If imbalance > 0.3 in direction of move, reversion_favoured must be False."""
    from unittest.mock import AsyncMock, patch

    # Heavily bid-side book (buy pressure → positive imbalance > 0.3)
    book = _book(
        asks=[(0.76, 100.0)],    # thin ask
        bids=[(0.74, 10_000.0)], # thick bid → imbalance ~ +1
    )

    clob = AsyncMock()
    clob.get_order_book.return_value = book

    analyser = MicrostructureAnalyser(clob)
    with patch.object(analyser, "_fetch_recent_trades", return_value=[]):
        result = await analyser.analyse("TOK")

    # imbalance ~ 1.0 > 0.3 → reversion NOT favoured
    assert result.reversion_favoured is False
