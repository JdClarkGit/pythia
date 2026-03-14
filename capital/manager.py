"""Capital manager: tracks free USDC, reserves for open orders, recycles profits."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from db.models import CapitalSnapshot
from db.store import Store
from utils.logger import get_logger

log = get_logger(__name__)


class CapitalManager:
    """Thread-safe capital ledger.

    Maintains the free USDC balance and tracks reserves committed to open
    orders so the allocator never over-commits.

    Args:
        initial_usdc: Starting USDC balance.
        store: :class:`~db.store.Store` instance for persisting snapshots.
    """

    def __init__(
        self,
        initial_usdc: float,
        store: Optional[Store] = None,
    ) -> None:
        self._total = initial_usdc
        self._free = initial_usdc
        self._reserved: dict[int, float] = {}   # trade_id -> reserved USDC
        self._realised_pnl: float = 0.0
        self._store = store
        self._lock = asyncio.Lock()

    @property
    def free_usdc(self) -> float:
        """USDC currently available for new trades."""
        return self._free

    @property
    def reserved_usdc(self) -> float:
        """USDC committed to open orders."""
        return sum(self._reserved.values())

    @property
    def total_usdc(self) -> float:
        """Total capital (free + reserved + realised P&L growth)."""
        return self._total

    @property
    def realised_pnl(self) -> float:
        """Cumulative realised P&L since start."""
        return self._realised_pnl

    async def reserve(self, trade_id: int, amount: float) -> bool:
        """Reserve capital for an open trade.

        Args:
            trade_id: Trade row ID.
            amount: USDC to reserve.

        Returns:
            ``True`` if sufficient capital was available and reserved.
        """
        async with self._lock:
            if amount > self._free + 1e-6:
                log.warning(
                    "Insufficient capital: need %.4f, have %.4f", amount, self._free
                )
                return False
            self._free -= amount
            self._reserved[trade_id] = amount
            log.debug(
                "Reserved %.4f USDC for trade %d (free=%.4f)", amount, trade_id, self._free
            )
            return True

    async def release(self, trade_id: int, *, pnl: float = 0.0) -> None:
        """Release a reservation and credit profit.

        Called when a trade is merged (success) or cancelled (no profit).

        Args:
            trade_id: Trade row ID to release.
            pnl: Net profit in USDC to add back to free capital
                 (negative for a loss, 0 for cancellation).
        """
        async with self._lock:
            reserved = self._reserved.pop(trade_id, 0.0)
            self._free += reserved + pnl
            self._total += pnl
            self._realised_pnl += pnl
            log.debug(
                "Released %.4f USDC (trade %d, pnl=%.4f) → free=%.4f",
                reserved,
                trade_id,
                pnl,
                self._free,
            )

    async def snapshot(self, open_positions_value: float = 0.0) -> CapitalSnapshot:
        """Create and optionally persist a capital snapshot.

        Args:
            open_positions_value: Mark-to-market value of open positions.

        Returns:
            :class:`~db.models.CapitalSnapshot` instance.
        """
        stats: dict[str, int | float] = {}
        if self._store:
            try:
                stats = await self._store.get_trade_stats()
            except Exception as exc:  # noqa: BLE001
                log.debug("Stats query failed: %s", exc)

        snap = CapitalSnapshot(
            usdc_balance=self._free,
            open_positions_value=open_positions_value,
            realised_pnl=self._realised_pnl,
            unrealised_pnl=open_positions_value - self.reserved_usdc,
            total_trades=int(stats.get("total", 0)),
            winning_trades=int(stats.get("merged", 0)),
            recorded_at=datetime.utcnow(),
        )
        if self._store:
            try:
                await self._store.insert_snapshot(snap)
            except Exception as exc:  # noqa: BLE001
                log.debug("Snapshot persist failed: %s", exc)
        return snap

    def status_line(self) -> str:
        """One-line human-readable capital summary.

        Returns:
            Formatted string.
        """
        return (
            f"Capital | free={self._free:.2f} USDC "
            f"reserved={self.reserved_usdc:.2f} "
            f"pnl={self._realised_pnl:+.4f}"
        )
