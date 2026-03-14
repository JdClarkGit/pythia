"""Position tracker: monitors open YES/NO token balances on-chain."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from db.models import Position
from db.store import Store
from utils.logger import get_logger

log = get_logger(__name__)


class PositionTracker:
    """Tracks open conditional-token positions by reconciling the DB with
    on-chain token balances.

    Args:
        store: :class:`~db.store.Store` for position persistence.
        wallet_address: The bot's Polygon wallet address.
        rpc_url: Polygon RPC endpoint.
    """

    def __init__(
        self,
        store: Store,
        wallet_address: str,
        rpc_url: str = "https://polygon-rpc.com",
    ) -> None:
        self._store = store
        self._wallet = wallet_address
        self._rpc_url = rpc_url
        self._positions: dict[str, Position] = {}

    async def load_from_db(self) -> None:
        """Load open positions from the database into memory.

        Should be called at startup to recover from any prior session.
        """
        positions = await self._store.get_open_positions()
        for pos in positions:
            self._positions[pos.condition_id] = pos
        log.info("Loaded %d open positions from DB", len(self._positions))

    def record_fill(
        self,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str,
        shares: float,
        usdc_cost: float,
    ) -> Position:
        """Record that YES/NO tokens were acquired for a market.

        Args:
            condition_id: Market condition ID.
            yes_token_id: ERC-1155 YES token ID.
            no_token_id: ERC-1155 NO token ID.
            shares: Number of share-pairs acquired.
            usdc_cost: Total USDC paid.

        Returns:
            Updated :class:`~db.models.Position`.
        """
        pos = self._positions.get(condition_id)
        if pos is None:
            pos = Position(
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
            )
            self._positions[condition_id] = pos

        pos.yes_amount += shares
        pos.no_amount += shares
        pos.usdc_cost += usdc_cost
        pos.updated_at = datetime.utcnow()
        return pos

    async def persist(self, condition_id: str) -> None:
        """Persist a position to the database.

        Args:
            condition_id: Market condition ID to persist.
        """
        pos = self._positions.get(condition_id)
        if pos:
            await self._store.upsert_position(pos)

    async def clear(self, condition_id: str) -> None:
        """Remove a position after a successful merge.

        Args:
            condition_id: Market condition ID to clear.
        """
        self._positions.pop(condition_id, None)
        await self._store.delete_position(condition_id)
        log.debug("Position cleared: %s", condition_id)

    def get_position(self, condition_id: str) -> Optional[Position]:
        """Return the in-memory position for a market.

        Args:
            condition_id: Market condition ID.

        Returns:
            :class:`~db.models.Position` or ``None``.
        """
        return self._positions.get(condition_id)

    def all_positions(self) -> list[Position]:
        """Return all open positions.

        Returns:
            List of :class:`~db.models.Position` instances.
        """
        return list(self._positions.values())

    def total_open_value(self) -> float:
        """Estimate mark-to-market value of all open positions.

        Approximates each YES+NO pair as worth $1.00.

        Returns:
            Total open value in USDC.
        """
        return sum(
            min(pos.yes_amount, pos.no_amount)
            for pos in self._positions.values()
        )
