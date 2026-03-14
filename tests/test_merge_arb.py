"""Unit tests for the MergeArbStrategy (all external calls mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capital.allocator import AllocationResult, KellyAllocator
from capital.manager import CapitalManager
from db.models import TradeStatus
from db.store import Store
from executor.merge_trigger import MergeTrigger
from executor.order_placer import OrderPlacer
from scanner.clob_client import OrderBook, PlacedOrder
from scanner.fee_calculator import MarketCategory
from scanner.gamma_client import MarketInfo, TokenInfo
from scanner.opportunity_detector import Opportunity
from strategy.merge_arb import MergeArbStrategy
from utils.alerts import Alerter


# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_opportunity(yes_ask: float = 0.48, no_ask: float = 0.49) -> Opportunity:
    book = OrderBook(token_id="0", asks=[], bids=[])
    market = MarketInfo(
        condition_id="0xdeadbeef" + "0" * 56,
        question="Test market",
        category="politics",
        tokens=[
            TokenInfo(token_id="111", outcome="Yes"),
            TokenInfo(token_id="222", outcome="No"),
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
        max_shares=100.0,
    )


async def _make_store() -> Store:
    store = Store(":memory:")
    await store.open()
    return store


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_dry_run_returns_trade():
    """Dry-run should return a Trade without calling the placer or merger."""
    store = await _make_store()

    capital = CapitalManager(300.0, store)
    allocator = KellyAllocator(kelly_fraction=0.25, max_trade_usdc=100.0)
    placer = AsyncMock(spec=OrderPlacer)
    merger = AsyncMock(spec=MergeTrigger)
    alerter = AsyncMock(spec=Alerter)

    strategy = MergeArbStrategy(
        capital, allocator, placer, merger, store, alerter, dry_run=True
    )

    opp = make_opportunity()
    trade = await strategy.execute(opp)

    assert trade is not None
    assert trade.net_profit > 0
    placer.place_both_market.assert_not_called()
    merger.merge.assert_not_called()

    await store.close()


@pytest.mark.asyncio
async def test_execute_full_success():
    """Happy-path: orders fill, merge succeeds, capital credited."""
    store = await _make_store()
    capital = CapitalManager(300.0, store)
    allocator = KellyAllocator(kelly_fraction=0.25, max_trade_usdc=100.0)

    yes_order = PlacedOrder(
        order_id="YES-001", status="matched", token_id="111", side="BUY", price=0.48, size=10
    )
    no_order = PlacedOrder(
        order_id="NO-001", status="matched", token_id="222", side="BUY", price=0.49, size=10
    )

    placer = AsyncMock(spec=OrderPlacer)
    placer.place_both_market.return_value = (yes_order, no_order)
    placer.wait_for_fills.return_value = True

    merger = AsyncMock(spec=MergeTrigger)
    merger.merge.return_value = "0x" + "a" * 64

    alerter = AsyncMock(spec=Alerter)

    strategy = MergeArbStrategy(capital, allocator, placer, merger, store, alerter)

    opp = make_opportunity()
    trade = await strategy.execute(opp)

    assert trade is not None
    assert trade.status == TradeStatus.MERGED
    assert trade.tx_hash is not None
    # Capital should have been returned + profit
    assert capital.free_usdc > 200  # some was reserved and returned

    await store.close()


@pytest.mark.asyncio
async def test_execute_order_placement_failure():
    """When order placement returns None, trade should be FAILED."""
    store = await _make_store()
    capital = CapitalManager(300.0, store)
    allocator = KellyAllocator(kelly_fraction=0.25, max_trade_usdc=100.0)

    placer = AsyncMock(spec=OrderPlacer)
    placer.place_both_market.return_value = (None, None)

    merger = AsyncMock(spec=MergeTrigger)
    alerter = AsyncMock(spec=Alerter)

    strategy = MergeArbStrategy(capital, allocator, placer, merger, store, alerter)

    opp = make_opportunity()
    trade = await strategy.execute(opp)

    # Should return None since placement failed
    assert trade is None
    merger.merge.assert_not_called()
    # Capital should be released back
    assert abs(capital.free_usdc - 300.0) < 1.0

    await store.close()


@pytest.mark.asyncio
async def test_execute_fill_timeout_cancels():
    """If fills timeout, orders cancelled and capital released."""
    store = await _make_store()
    capital = CapitalManager(300.0, store)
    allocator = KellyAllocator(kelly_fraction=0.25, max_trade_usdc=100.0)

    yes_order = PlacedOrder(
        order_id="Y", status="open", token_id="111", side="BUY", price=0.48, size=10
    )
    no_order = PlacedOrder(
        order_id="N", status="open", token_id="222", side="BUY", price=0.49, size=10
    )

    placer = AsyncMock(spec=OrderPlacer)
    placer.place_both_market.return_value = (yes_order, no_order)
    placer.wait_for_fills.return_value = False
    placer.cancel_orders = AsyncMock()

    merger = AsyncMock(spec=MergeTrigger)
    alerter = AsyncMock(spec=Alerter)

    strategy = MergeArbStrategy(capital, allocator, placer, merger, store, alerter)

    opp = make_opportunity()
    trade = await strategy.execute(opp)

    # Strategy returns None on fill timeout
    assert trade is None
    placer.cancel_orders.assert_called_once_with("Y", "N")
    merger.merge.assert_not_called()
    # Capital returned (no profit)
    assert abs(capital.free_usdc - 300.0) < 1.0

    await store.close()


@pytest.mark.asyncio
async def test_insufficient_capital_skips():
    """If free capital too small, allocation returns zero shares → skip."""
    store = await _make_store()
    capital = CapitalManager(0.50, store)  # almost nothing
    allocator = KellyAllocator(min_trade_usdc=5.0)

    placer = AsyncMock(spec=OrderPlacer)
    merger = AsyncMock(spec=MergeTrigger)
    alerter = AsyncMock(spec=Alerter)

    strategy = MergeArbStrategy(capital, allocator, placer, merger, store, alerter)

    opp = make_opportunity()
    trade = await strategy.execute(opp)

    assert trade is None
    placer.place_both_market.assert_not_called()

    await store.close()
