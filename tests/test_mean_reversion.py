"""Tests for the mean-reversion and price-magnet strategies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capital.allocator import AllocationResult, enhanced_kelly, EnhancedAllocationResult
from capital.manager import CapitalManager
from db.store import Store
from scanner.clob_client import OrderBook, PriceLevel, PlacedOrder
from scanner.gamma_client import MarketInfo, TokenInfo
from strategy.mean_reversion import (
    MeanReversionStrategy,
    MeanReversionCandidate,
    _depth_near_price,
    _has_enough_time,
    _hist_kelly_params,
    _hist_success_rate,
)
from strategy.price_magnet import (
    PriceMagnetStrategy,
    PriceMagnetCandidate,
    _is_volume_spike,
)


# ─── Fixtures / helpers ───────────────────────────────────────────────────────


def _make_market(
    yes_price: float = 0.10,
    no_price: float = 0.90,
    volume: float = 1_000.0,
    days_to_end: int = 60,
    condition_id: str = "0xAABB",
) -> MarketInfo:
    end = datetime.now(tz=timezone.utc) + timedelta(days=days_to_end)
    return MarketInfo(
        condition_id=condition_id,
        question="Will X happen by year-end?",
        category="politics",
        tags=["politics"],
        tokens=[
            TokenInfo(token_id="111", outcome="Yes", price=yes_price),
            TokenInfo(token_id="222", outcome="No", price=no_price),
        ],
        volume=volume,
        liquidity=10_000.0,
        end_date=end,
    )


def _make_book(best_ask: float, depth_size: float = 1000.0) -> OrderBook:
    """Build a simple order book with one ask and one bid level."""
    return OrderBook(
        token_id="111",
        asks=[PriceLevel(price=best_ask, size=depth_size)],
        bids=[PriceLevel(price=best_ask - 0.01, size=depth_size)],
    )


async def _make_store() -> Store:
    store = Store(":memory:")
    await store.open()
    return store


# ─── Unit tests: pure helper functions ───────────────────────────────────────


def test_hist_success_rate_buckets():
    """Combined probabilities across both positive exit scenarios."""
    # 0–5c: p_90c=0.03 + p_50c=0.03 = 0.06
    assert _hist_success_rate(0.04) == pytest.approx(0.06)
    # 5–10c: p_90c=0.06 + p_50c=0.13 = 0.19
    assert _hist_success_rate(0.08) == pytest.approx(0.19)
    # 10–15c: p_90c=0.10 + p_50c=0.13 = 0.23
    assert _hist_success_rate(0.14) == pytest.approx(0.23)


def test_hist_kelly_params_positive_ev():
    """Weighted b and combined p should give positive Kelly for 10c bucket."""
    p, b = _hist_kelly_params(0.08)
    assert p == pytest.approx(0.19)
    assert b > 0
    # Verify Kelly > 0
    kelly = (b * p - (1 - p)) / b
    assert kelly > 0, f"Kelly {kelly:.4f} is not positive for p={p}, b={b}"


def test_depth_near_price():
    book = OrderBook(
        token_id="t",
        asks=[
            PriceLevel(price=0.10, size=100.0),
            PriceLevel(price=0.11, size=50.0),
            PriceLevel(price=0.20, size=200.0),  # too far away
        ],
        bids=[],
    )
    depth = _depth_near_price(book, price=0.10, tolerance=0.02)
    # 0.10 * 100 + 0.11 * 50 = 10 + 5.5 = 15.5 USDC
    assert depth == pytest.approx(15.5)


def test_has_enough_time_passes():
    market = _make_market(days_to_end=60)
    assert _has_enough_time(market, min_days=30) is True


def test_has_enough_time_fails():
    market = _make_market(days_to_end=15)
    assert _has_enough_time(market, min_days=30) is False


def test_has_enough_time_no_end_date():
    """Markets without end_date are allowed through (conservative)."""
    market = MarketInfo(
        condition_id="0xNOEND",
        question="No date",
        tokens=[TokenInfo(token_id="1", outcome="Yes"), TokenInfo(token_id="2", outcome="No")],
    )
    assert _has_enough_time(market, min_days=30) is True


def test_is_volume_spike_low_volume():
    market = _make_market(volume=10_000.0)
    assert _is_volume_spike(market) is False


def test_is_volume_spike_high_volume():
    market = _make_market(volume=600_000.0)
    assert _is_volume_spike(market) is True


# ─── Enhanced Kelly tests ─────────────────────────────────────────────────────


def test_enhanced_kelly_positive_ev():
    """5.26x return at 19% combined probability (10c bucket) yields positive Kelly.

    Using the combined multi-outcome probability from the mean-reversion strategy:
    - p_combined = 0.06 (→90c) + 0.13 (→50c) = 0.19
    - b_weighted = (0.06*8 + 0.13*4) / 0.19 ≈ 5.26
    - Standard Kelly = (5.26*0.19 - 0.81) / 5.26 ≈ 0.036  (positive)
    """
    result = enhanced_kelly(
        b=5.26,
        p=0.19,
        depth_yes=2000.0,
        depth_no=0.0,
        trade_size_usdc=50.0,
        fractional=0.25,
        available_usdc=1000.0,
    )
    assert isinstance(result, EnhancedAllocationResult)
    assert result.usdc_amount > 0
    assert result.usdc_amount <= 1000.0
    assert 0.0 <= result.p_execution <= 1.0


def test_enhanced_kelly_negative_ev():
    """Very low win probability should return zero allocation (negative EV)."""
    result = enhanced_kelly(b=2.0, p=0.01, available_usdc=1000.0)
    assert result.usdc_amount == 0.0


def test_enhanced_kelly_respects_max_usdc():
    result = enhanced_kelly(
        b=8.0,
        p=0.19,
        depth_yes=10_000.0,
        depth_no=0.0,
        trade_size_usdc=100.0,
        fractional=0.25,
        max_usdc=10.0,
        available_usdc=1000.0,
    )
    assert result.usdc_amount <= 10.0


def test_enhanced_kelly_book_cap():
    """Allocation must not exceed 50 % of the shallower book side."""
    result = enhanced_kelly(
        b=8.0,
        p=0.19,
        depth_yes=100.0,    # small depth
        depth_no=0.0,
        trade_size_usdc=50.0,
        fractional=0.25,
        available_usdc=10_000.0,
    )
    # 50 % of 100 USDC depth = 50 USDC cap
    assert result.usdc_amount <= 50.0


def test_enhanced_kelly_invalid_inputs():
    assert enhanced_kelly(b=0.0, p=0.5, available_usdc=1000.0).usdc_amount == 0.0
    assert enhanced_kelly(b=1.0, p=0.0, available_usdc=1000.0).usdc_amount == 0.0
    assert enhanced_kelly(b=1.0, p=1.0, available_usdc=1000.0).usdc_amount == 0.0


# ─── Strategy integration tests (mocked I/O) ─────────────────────────────────


@pytest.mark.asyncio
async def test_mean_reversion_scan_filters_by_time():
    """Markets expiring in < 30 days must be excluded."""
    gamma = AsyncMock()
    clob = AsyncMock()
    alerter = AsyncMock()
    store = await _make_store()
    capital = CapitalManager(1000.0, store)

    # Market 1: expires in 10 days — should be skipped
    m_short = _make_market(yes_price=0.08, days_to_end=10, condition_id="0xSHORT")
    # Market 2: expires in 60 days — eligible
    m_long = _make_market(yes_price=0.08, days_to_end=60, condition_id="0xLONG")

    gamma.get_all_active_markets.return_value = [m_short, m_long]

    # Provide a deep enough order book for m_long
    deep_book = _make_book(best_ask=0.08, depth_size=10_000.0)
    empty_book = _make_book(best_ask=0.92, depth_size=10_000.0)
    clob.get_order_book.return_value = deep_book

    strategy = MeanReversionStrategy(
        gamma, clob, capital, store, alerter, dry_run=True, use_microstructure=False
    )
    candidates = await strategy.scan()

    # Only the long-dated market should produce a candidate
    condition_ids = {c.market.condition_id for c in candidates}
    assert "0xSHORT" not in condition_ids
    assert "0xLONG" in condition_ids

    await store.close()


@pytest.mark.asyncio
async def test_mean_reversion_scan_filters_by_depth():
    """Markets with < $500 depth at entry price must be skipped."""
    gamma = AsyncMock()
    clob = AsyncMock()
    alerter = AsyncMock()
    store = await _make_store()
    capital = CapitalManager(1000.0, store)

    market = _make_market(yes_price=0.10, days_to_end=60)
    gamma.get_all_active_markets.return_value = [market]

    # Shallow book: 0.10 * 10 = $1 USDC depth — below $500 threshold
    shallow_book = _make_book(best_ask=0.10, depth_size=10.0)
    clob.get_order_book.return_value = shallow_book

    strategy = MeanReversionStrategy(
        gamma, clob, capital, store, alerter, dry_run=True, use_microstructure=False
    )
    candidates = await strategy.scan()
    assert len(candidates) == 0

    await store.close()


@pytest.mark.asyncio
async def test_mean_reversion_scan_finds_deep_underdog():
    """A token at 8 c with deep liquidity and 60 days should qualify."""
    gamma = AsyncMock()
    clob = AsyncMock()
    alerter = AsyncMock()
    store = await _make_store()
    capital = CapitalManager(1000.0, store)

    market = _make_market(yes_price=0.08, no_price=0.92, days_to_end=60)
    gamma.get_all_active_markets.return_value = [market]

    # Deep book: 0.08 * 10_000 = $800 USDC depth — above $500 threshold
    deep_yes_book = _make_book(best_ask=0.08, depth_size=10_000.0)
    deep_no_book = _make_book(best_ask=0.92, depth_size=10_000.0)

    async def get_book(token_id: str) -> OrderBook:
        return deep_yes_book if token_id == "111" else deep_no_book

    clob.get_order_book.side_effect = get_book

    strategy = MeanReversionStrategy(
        gamma, clob, capital, store, alerter, dry_run=True, use_microstructure=False
    )
    candidates = await strategy.scan()

    assert len(candidates) >= 1
    c = candidates[0]
    assert c.side == "YES"
    assert c.entry_price == pytest.approx(0.08)
    assert c.target_price == pytest.approx(0.90)
    assert c.stop_loss == pytest.approx(0.005)
    assert c.recommended_usdc > 0

    await store.close()


@pytest.mark.asyncio
async def test_mean_reversion_execute_dry_run():
    """Dry-run must return a position object without calling CLOB."""
    gamma = AsyncMock()
    clob = AsyncMock()
    alerter = AsyncMock()
    store = await _make_store()
    capital = CapitalManager(1000.0, store)

    strategy = MeanReversionStrategy(
        gamma, clob, capital, store, alerter, dry_run=True, use_microstructure=False
    )

    candidate = MeanReversionCandidate(
        market=_make_market(),
        token_id="111",
        side="YES",
        entry_price=0.08,
        target_price=0.90,
        stop_loss=0.005,
        depth_usdc=1000.0,
        hist_p=0.06,
        recommended_usdc=20.0,
    )

    position = await strategy.execute(candidate)
    assert position is not None
    assert position.strategy_type == "extreme_reversion"
    assert position.entry_price == pytest.approx(0.08)
    assert position.target_price == pytest.approx(0.90)
    clob.place_limit_order.assert_not_called()

    await store.close()


@pytest.mark.asyncio
async def test_price_magnet_scan_detects_high_zone():
    """A market with YES at 75 c should produce a NO BUY candidate."""
    gamma = AsyncMock()
    clob = AsyncMock()
    alerter = AsyncMock()
    store = await _make_store()
    capital = CapitalManager(1000.0, store)

    market = _make_market(yes_price=0.75, no_price=0.25, volume=1_000.0, days_to_end=90)
    gamma.get_all_active_markets.return_value = [market]

    # YES book: mid at 75 c
    yes_book = OrderBook(
        token_id="111",
        asks=[PriceLevel(price=0.76, size=5000.0)],
        bids=[PriceLevel(price=0.74, size=5000.0)],  # mid = 0.75
    )
    # NO book: best ask at 0.25
    no_book = OrderBook(
        token_id="222",
        asks=[PriceLevel(price=0.25, size=5000.0)],
        bids=[PriceLevel(price=0.24, size=5000.0)],
    )

    async def get_book(token_id: str) -> OrderBook:
        return yes_book if token_id == "111" else no_book

    clob.get_order_book.side_effect = get_book

    strategy = PriceMagnetStrategy(
        gamma, clob, capital, store, alerter,
        microstructure=None, dry_run=True
    )
    candidates = await strategy.scan()

    assert len(candidates) >= 1
    c = candidates[0]
    assert c.side == "NO"
    assert c.token_id == "222"
    assert c.entry_price == pytest.approx(0.25)
    assert c.target_price == pytest.approx(0.50)

    await store.close()


@pytest.mark.asyncio
async def test_price_magnet_scan_skips_volume_spike():
    """Markets with volume ≥ 500 k should be skipped as info-driven."""
    gamma = AsyncMock()
    clob = AsyncMock()
    alerter = AsyncMock()
    store = await _make_store()
    capital = CapitalManager(1000.0, store)

    market = _make_market(yes_price=0.75, no_price=0.25, volume=600_000.0)
    gamma.get_all_active_markets.return_value = [market]

    yes_book = OrderBook(
        token_id="111",
        asks=[PriceLevel(price=0.76, size=5000.0)],
        bids=[PriceLevel(price=0.74, size=5000.0)],
    )
    no_book = _make_book(best_ask=0.25, depth_size=5000.0)

    async def get_book(token_id: str) -> OrderBook:
        return yes_book if token_id == "111" else no_book

    clob.get_order_book.side_effect = get_book

    strategy = PriceMagnetStrategy(
        gamma, clob, capital, store, alerter,
        microstructure=None, dry_run=True
    )
    candidates = await strategy.scan()
    assert len(candidates) == 0

    await store.close()


@pytest.mark.asyncio
async def test_price_magnet_execute_dry_run():
    """Dry-run should return a position without calling the CLOB."""
    gamma = AsyncMock()
    clob = AsyncMock()
    alerter = AsyncMock()
    store = await _make_store()
    capital = CapitalManager(1000.0, store)

    strategy = PriceMagnetStrategy(
        gamma, clob, capital, store, alerter,
        microstructure=None, dry_run=True
    )

    candidate = PriceMagnetCandidate(
        market=_make_market(yes_price=0.75),
        token_id="222",
        side="NO",
        yes_price=0.75,
        entry_price=0.25,
        target_price=0.50,
        stop_loss=0.85,
        depth_usdc=500.0,
        recommended_usdc=25.0,
    )

    position = await strategy.execute(candidate)
    assert position is not None
    assert position.strategy_type == "price_magnet"
    assert position.entry_price == pytest.approx(0.25)
    assert position.target_price == pytest.approx(0.50)
    clob.place_limit_order.assert_not_called()

    await store.close()
