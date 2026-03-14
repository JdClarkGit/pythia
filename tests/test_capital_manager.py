"""Unit tests for CapitalManager and KellyAllocator."""

from __future__ import annotations

import pytest

from capital.allocator import AllocationResult, KellyAllocator
from capital.manager import CapitalManager
from scanner.clob_client import OrderBook
from scanner.fee_calculator import MarketCategory
from scanner.gamma_client import MarketInfo, TokenInfo
from scanner.opportunity_detector import Opportunity


# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_opportunity(yes_ask: float = 0.45, no_ask: float = 0.48) -> Opportunity:
    book = OrderBook(token_id="0", asks=[{"price": yes_ask, "size": 1000}], bids=[])
    market = MarketInfo(
        condition_id="0x" + "1" * 64,
        question="Test",
        tokens=[
            TokenInfo(token_id="1", outcome="Yes"),
            TokenInfo(token_id="2", outcome="No"),
        ],
    )
    net = 1.0 - yes_ask - no_ask
    return Opportunity(
        market=market,
        category=MarketCategory.ZERO_FEE,
        yes_ask=yes_ask,
        no_ask=no_ask,
        yes_book=book,
        no_book=book,
        gross_profit_pct=net * 100,
        net_profit_pct=net * 100,
        net_profit_per_share=net,
        max_shares=1000.0,
    )


# ─── CapitalManager ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reserve_and_release():
    mgr = CapitalManager(300.0)
    ok = await mgr.reserve(1, 100.0)
    assert ok
    assert abs(mgr.free_usdc - 200.0) < 1e-9
    assert abs(mgr.reserved_usdc - 100.0) < 1e-9

    await mgr.release(1, pnl=5.0)
    assert abs(mgr.free_usdc - 305.0) < 1e-9
    assert mgr.reserved_usdc == 0.0
    assert abs(mgr.realised_pnl - 5.0) < 1e-9


@pytest.mark.asyncio
async def test_reserve_insufficient():
    mgr = CapitalManager(50.0)
    ok = await mgr.reserve(1, 100.0)
    assert not ok
    assert mgr.free_usdc == 50.0


@pytest.mark.asyncio
async def test_reserve_multiple_trades():
    mgr = CapitalManager(300.0)
    await mgr.reserve(1, 100.0)
    await mgr.reserve(2, 80.0)
    assert abs(mgr.reserved_usdc - 180.0) < 1e-9
    assert abs(mgr.free_usdc - 120.0) < 1e-9

    await mgr.release(1, pnl=2.0)
    assert abs(mgr.free_usdc - 222.0) < 1e-9  # 120 + 100 + 2


@pytest.mark.asyncio
async def test_release_unknown_trade_is_noop():
    mgr = CapitalManager(100.0)
    await mgr.release(999, pnl=0.0)  # should not crash
    assert mgr.free_usdc == 100.0


@pytest.mark.asyncio
async def test_status_line():
    mgr = CapitalManager(300.0)
    await mgr.reserve(1, 50.0)
    line = mgr.status_line()
    assert "250.00" in line
    assert "50.00" in line


# ─── KellyAllocator ──────────────────────────────────────────────────────────


def test_allocator_basic():
    allocator = KellyAllocator(kelly_fraction=0.25, max_trade_usdc=100.0)
    opp = make_opportunity(0.45, 0.48)  # 7% edge
    result = allocator.allocate(opp, available_usdc=300.0)
    assert result.shares > 0
    assert result.usdc_amount > 0
    assert result.usdc_amount <= 100.0  # capped


def test_allocator_respects_max_trade():
    allocator = KellyAllocator(kelly_fraction=1.0, max_trade_usdc=50.0)
    opp = make_opportunity(0.40, 0.40)  # 20% edge
    result = allocator.allocate(opp, available_usdc=1000.0)
    assert result.usdc_amount <= 50.0


def test_allocator_respects_max_allocation_pct():
    allocator = KellyAllocator(max_allocation_pct=0.10, max_trade_usdc=10_000.0)
    opp = make_opportunity(0.45, 0.48)
    result = allocator.allocate(opp, available_usdc=300.0)
    # Should not exceed 10% of 300 = 30 USDC
    assert result.usdc_amount <= 30.0 + 1e-9


def test_allocator_zero_when_insufficient():
    allocator = KellyAllocator(min_trade_usdc=10.0)
    opp = make_opportunity(0.45, 0.48)
    result = allocator.allocate(opp, available_usdc=2.0)
    assert result.shares == 0.0
    assert result.usdc_amount == 0.0


def test_allocator_liquidity_cap():
    """If available liquidity is tiny, allocation should respect it."""
    book = OrderBook(
        token_id="1",
        asks=[{"price": 0.45, "size": 1.0}],  # only 1 USDC of depth
        bids=[],
    )
    market = MarketInfo(
        condition_id="0x" + "2" * 64,
        question="Illiquid market",
        tokens=[
            TokenInfo(token_id="1", outcome="Yes"),
            TokenInfo(token_id="2", outcome="No"),
        ],
    )
    opp = Opportunity(
        market=market,
        category=MarketCategory.ZERO_FEE,
        yes_ask=0.45,
        no_ask=0.48,
        yes_book=book,
        no_book=book,
        gross_profit_pct=7.0,
        net_profit_pct=7.0,
        net_profit_per_share=0.07,
        max_shares=1.0,  # only 1 share of liquidity
    )
    allocator = KellyAllocator(max_trade_usdc=1000.0)
    result = allocator.allocate(opp, available_usdc=1000.0)
    # Should be capped at 1 share
    assert result.shares <= 1.0 + 1e-9


def test_allocator_kelly_fraction_is_recorded():
    allocator = KellyAllocator(kelly_fraction=0.25)
    opp = make_opportunity()
    result = allocator.allocate(opp, available_usdc=300.0)
    assert result.kelly_fraction > 0
    assert result.kelly_fraction < 1.0
