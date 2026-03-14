"""Maker limit-order arb strategy.

Places limit (maker) orders slightly inside the spread to earn zero taker fees
and daily USDC rebates.  Once both legs fill, triggers on-chain merge.

Strategy:
  - YES limit order: bid at (yes_ask - spread_buffer)
  - NO  limit order: bid at (no_ask  - spread_buffer)
  - If both fill: net_profit = 1.00 - yes_fill - no_fill (zero fees)
  - Rebate: 20-25% of fee pool redistributed daily to makers
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from capital.allocator import KellyAllocator
from capital.manager import CapitalManager
from db.models import Trade, TradeStatus
from db.store import Store
from executor.merge_trigger import MergeTrigger
from executor.order_placer import OrderPlacer
from scanner.opportunity_detector import Opportunity
from utils.alerts import Alerter
from utils.logger import get_logger

log = get_logger(__name__)


class MakerArbStrategy:
    """Execute merge arb with maker (limit) orders to avoid taker fees.

    Args:
        capital: :class:`~capital.manager.CapitalManager` instance.
        allocator: :class:`~capital.allocator.KellyAllocator` instance.
        placer: :class:`~executor.order_placer.OrderPlacer` instance.
        merger: :class:`~executor.merge_trigger.MergeTrigger` instance.
        store: :class:`~db.store.Store` for persistence.
        alerter: :class:`~utils.alerts.Alerter` for notifications.
        spread_buffer: How far inside the ask to place limit price
                       (e.g. ``0.002`` = 0.2 ¢ below best ask).
        order_timeout: Seconds to wait for fills before cancelling.
        dry_run: If ``True``, simulate without real orders.
    """

    def __init__(
        self,
        capital: CapitalManager,
        allocator: KellyAllocator,
        placer: OrderPlacer,
        merger: MergeTrigger,
        store: Store,
        alerter: Alerter,
        *,
        spread_buffer: float = 0.002,
        order_timeout: int = 300,
        dry_run: bool = False,
    ) -> None:
        self._capital = capital
        self._allocator = allocator
        self._placer = placer
        self._merger = merger
        self._store = store
        self._alerter = alerter
        self._spread_buffer = spread_buffer
        self._order_timeout = order_timeout
        self._dry_run = dry_run

    async def execute(self, opp: Opportunity) -> Optional[Trade]:
        """Execute one maker merge-arb opportunity.

        Places limit orders one tick inside the best ask.  Waits up to
        ``order_timeout`` seconds for fills, then cancels if not filled.

        Args:
            opp: Detected :class:`~scanner.opportunity_detector.Opportunity`.

        Returns:
            Completed :class:`~db.models.Trade`, or ``None`` if skipped.
        """
        alloc = self._allocator.allocate(opp, self._capital.free_usdc)
        if alloc.shares < 1e-6:
            log.info("Allocation zero for %s — skipping", opp.market.condition_id)
            return None

        shares = alloc.shares
        # Maker prices are slightly inside the ask (we become the new best bid)
        yes_limit = round(opp.yes_ask - self._spread_buffer, 4)
        no_limit = round(opp.no_ask - self._spread_buffer, 4)

        if yes_limit <= 0 or no_limit <= 0:
            log.warning("Limit prices would be non-positive, falling back to ask price")
            yes_limit = opp.yes_ask
            no_limit = opp.no_ask

        # Net profit for maker: no fees
        net_profit = (1.0 - yes_limit - no_limit) * shares

        trade = Trade(
            condition_id=opp.market.condition_id,
            market_question=opp.market.question,
            market_category=opp.market.category,
            yes_token_id=opp.market.yes_token_id or "",
            no_token_id=opp.market.no_token_id or "",
            yes_ask=yes_limit,
            no_ask=no_limit,
            fee_total=0.0,
            amount_usdc=alloc.usdc_amount,
            shares=shares,
            gross_profit=net_profit,
            net_profit=net_profit,
            status=TradeStatus.PENDING,
        )

        if self._dry_run:
            log.info(
                "[DRY-RUN] Maker orders | %s | YES@%.4f NO@%.4f shares=%.4f pnl≈%.6f",
                opp.market.question[:50],
                yes_limit,
                no_limit,
                shares,
                net_profit,
            )
            return trade

        trade_id = await self._store.insert_trade(trade)
        trade.id = trade_id

        reserved = await self._capital.reserve(trade_id, alloc.usdc_amount)
        if not reserved:
            await self._store.update_trade_status(trade_id, TradeStatus.CANCELLED, error="Insufficient capital")
            return None

        # Place limit orders simultaneously
        yes_order, no_order = await self._placer.place_both_limit(
            yes_token_id=opp.market.yes_token_id or "",
            no_token_id=opp.market.no_token_id or "",
            yes_price=yes_limit,
            no_price=no_limit,
            shares=shares,
        )

        if yes_order is None or no_order is None:
            log.error("Limit order placement failed for %s", opp.market.condition_id)
            await self._store.update_trade_status(trade_id, TradeStatus.FAILED, error="Order placement failed")
            await self._capital.release(trade_id, pnl=0.0)
            return None

        await self._store.update_trade_orders(
            trade_id, yes_order.order_id, no_order.order_id
        )
        log.info(
            "Limit orders placed | %s | YES=%s NO=%s",
            opp.market.condition_id[:12],
            yes_order.order_id,
            no_order.order_id,
        )

        # Wait for fills with timeout
        try:
            filled = await asyncio.wait_for(
                self._placer.wait_for_fills(yes_order.order_id, no_order.order_id),
                timeout=self._order_timeout,
            )
        except asyncio.TimeoutError:
            filled = False

        if not filled:
            log.info(
                "Limit orders expired/unfilled for %s — cancelling",
                opp.market.condition_id,
            )
            await self._placer.cancel_orders(yes_order.order_id, no_order.order_id)
            await self._store.update_trade_status(
                trade_id, TradeStatus.CANCELLED, error="Fill timeout"
            )
            await self._capital.release(trade_id, pnl=0.0)
            return None

        await self._store.update_trade_status(
            trade_id, TradeStatus.FILLED, filled_at=datetime.utcnow()
        )

        # On-chain merge
        tx_hash = await self._merger.merge(
            condition_id=opp.market.condition_id,
            shares=shares,
        )

        if tx_hash:
            await self._store.update_trade_status(
                trade_id,
                TradeStatus.MERGED,
                tx_hash=tx_hash,
                merged_at=datetime.utcnow(),
            )
            await self._capital.release(trade_id, pnl=net_profit)
            await self._alerter.send(
                f"[Maker] Merged {opp.market.question[:40]} | +{net_profit:.4f} USDC",
                level="info",
            )
            log.info(
                "MERGED (maker) | %s | pnl=+%.6f | tx=%s",
                opp.market.question[:50],
                net_profit,
                tx_hash,
            )
        else:
            await self._store.update_trade_status(
                trade_id, TradeStatus.FAILED, error="Merge tx failed"
            )
            await self._capital.release(trade_id, pnl=0.0)

        trade.status = TradeStatus.MERGED if tx_hash else TradeStatus.FAILED
        trade.tx_hash = tx_hash
        return trade
