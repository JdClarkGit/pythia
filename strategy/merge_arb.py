"""Taker merge-arb strategy.

Buys YES and NO at the current market (taker) prices, then immediately
triggers a mergePositions() on-chain to collect $1.00 USDC.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from capital.manager import CapitalManager
from capital.allocator import KellyAllocator
from db.models import Trade, TradeStatus
from db.store import Store
from executor.order_placer import OrderPlacer
from executor.merge_trigger import MergeTrigger
from scanner.fee_calculator import FeeCalculator
from scanner.opportunity_detector import Opportunity
from utils.alerts import Alerter
from utils.logger import get_logger

log = get_logger(__name__)


class MergeArbStrategy:
    """Execute a merge arb trade using market (taker) orders.

    Flow:
        1. Allocate capital via Kelly sizer.
        2. Place market BUY orders for YES and NO simultaneously.
        3. Wait for fills (or timeout → cancel).
        4. Call :meth:`~executor.merge_trigger.MergeTrigger.merge` to
           invoke ``mergePositions()`` on-chain.
        5. Credit profit back to the capital manager.

    Args:
        capital: :class:`~capital.manager.CapitalManager` instance.
        allocator: :class:`~capital.allocator.KellyAllocator` instance.
        placer: :class:`~executor.order_placer.OrderPlacer` instance.
        merger: :class:`~executor.merge_trigger.MergeTrigger` instance.
        store: :class:`~db.store.Store` for persistence.
        alerter: :class:`~utils.alerts.Alerter` for notifications.
        fee_calc: :class:`~scanner.fee_calculator.FeeCalculator`.
        dry_run: If ``True``, log actions without executing real orders.
    """

    def __init__(
        self,
        capital: CapitalManager,
        allocator: KellyAllocator,
        placer: OrderPlacer,
        merger: MergeTrigger,
        store: Store,
        alerter: Alerter,
        fee_calc: Optional[FeeCalculator] = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self._capital = capital
        self._allocator = allocator
        self._placer = placer
        self._merger = merger
        self._store = store
        self._alerter = alerter
        self._fee = fee_calc or FeeCalculator()
        self._dry_run = dry_run

    async def execute(self, opp: Opportunity) -> Optional[Trade]:
        """Execute one merge-arb opportunity end-to-end.

        Args:
            opp: :class:`~scanner.opportunity_detector.Opportunity` to trade.

        Returns:
            Completed :class:`~db.models.Trade` record, or ``None`` if
            the trade was skipped/failed before order placement.
        """
        alloc = self._allocator.allocate(opp, self._capital.free_usdc)
        if alloc.shares < 1e-6:
            log.info("Allocation zero for %s — skipping", opp.market.condition_id)
            return None

        shares = alloc.shares
        fee_total = (
            self._fee.taker_fee_usdc(opp.yes_ask, shares, opp.category)
            + self._fee.taker_fee_usdc(opp.no_ask, shares, opp.category)
        )
        gross_profit = (1.0 - opp.yes_ask - opp.no_ask) * shares
        net_profit = gross_profit - fee_total

        trade = Trade(
            condition_id=opp.market.condition_id,
            market_question=opp.market.question,
            market_category=opp.market.category,
            yes_token_id=opp.market.yes_token_id or "",
            no_token_id=opp.market.no_token_id or "",
            yes_ask=opp.yes_ask,
            no_ask=opp.no_ask,
            fee_total=fee_total,
            amount_usdc=alloc.usdc_amount,
            shares=shares,
            gross_profit=gross_profit,
            net_profit=net_profit,
            status=TradeStatus.PENDING,
        )

        if self._dry_run:
            log.info(
                "[DRY-RUN] Would trade %s | shares=%.4f | net_profit=%.6f USDC",
                opp.market.question[:50],
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

        # Place both orders simultaneously
        yes_order, no_order = await self._placer.place_both_market(
            yes_token_id=opp.market.yes_token_id or "",
            no_token_id=opp.market.no_token_id or "",
            shares=shares,
        )

        if yes_order is None or no_order is None:
            log.error("Order placement failed for %s", opp.market.condition_id)
            await self._store.update_trade_status(trade_id, TradeStatus.FAILED, error="Order placement failed")
            await self._capital.release(trade_id, pnl=0.0)
            await self._alerter.send(
                f"Order placement FAILED: {opp.market.question[:50]}", level="error"
            )
            return None

        await self._store.update_trade_orders(
            trade_id,
            yes_order.order_id,
            no_order.order_id,
        )

        # Wait for fills
        filled = await self._placer.wait_for_fills(
            yes_order.order_id, no_order.order_id
        )
        if not filled:
            log.warning("Orders did not fill for %s", opp.market.condition_id)
            await self._placer.cancel_orders(yes_order.order_id, no_order.order_id)
            await self._store.update_trade_status(trade_id, TradeStatus.CANCELLED, error="Fill timeout")
            await self._capital.release(trade_id, pnl=0.0)
            return None

        await self._store.update_trade_status(
            trade_id, TradeStatus.FILLED, filled_at=datetime.utcnow()
        )

        # Execute on-chain merge
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
                f"Merged {opp.market.question[:40]} | +{net_profit:.4f} USDC | tx={tx_hash[:10]}…",
                level="info",
            )
            log.info(
                "MERGED | %s | shares=%.4f | pnl=+%.6f | tx=%s",
                opp.market.question[:50],
                shares,
                net_profit,
                tx_hash,
            )
        else:
            await self._store.update_trade_status(trade_id, TradeStatus.FAILED, error="Merge tx failed")
            await self._capital.release(trade_id, pnl=0.0)
            await self._alerter.send(
                f"Merge FAILED: {opp.market.question[:40]}", level="error"
            )

        trade.status = TradeStatus.MERGED if tx_hash else TradeStatus.FAILED
        trade.tx_hash = tx_hash
        return trade
