"""Order placement and fill monitoring for YES/NO legs."""

from __future__ import annotations

import asyncio
from typing import Optional

from scanner.clob_client import CLOBClient, PlacedOrder
from utils.logger import get_logger

log = get_logger(__name__)

_FILL_POLL_SECONDS = 5
_FILL_TIMEOUT_SECONDS = 300


class OrderPlacer:
    """Handles simultaneous placement and monitoring of YES/NO order pairs.

    Args:
        clob: Authenticated :class:`~scanner.clob_client.CLOBClient`.
        fill_poll_seconds: Interval between fill-status polls.
        fill_timeout_seconds: Maximum wait time before declaring unfilled.
    """

    def __init__(
        self,
        clob: CLOBClient,
        fill_poll_seconds: int = _FILL_POLL_SECONDS,
        fill_timeout_seconds: int = _FILL_TIMEOUT_SECONDS,
    ) -> None:
        self._clob = clob
        self._poll = fill_poll_seconds
        self._timeout = fill_timeout_seconds

    async def place_both_market(
        self,
        yes_token_id: str,
        no_token_id: str,
        shares: float,
    ) -> tuple[Optional[PlacedOrder], Optional[PlacedOrder]]:
        """Place market BUY orders for both YES and NO simultaneously.

        Args:
            yes_token_id: ERC-1155 token ID for YES.
            no_token_id: ERC-1155 token ID for NO.
            shares: Number of tokens to buy.

        Returns:
            Tuple of (yes_order, no_order); either may be ``None`` on failure.
        """
        yes_order, no_order = await asyncio.gather(
            self._clob.place_market_order(yes_token_id, "BUY", shares),
            self._clob.place_market_order(no_token_id, "BUY", shares),
        )
        log.debug(
            "Market orders placed | YES=%s NO=%s",
            yes_order.order_id if yes_order else "FAILED",
            no_order.order_id if no_order else "FAILED",
        )
        return yes_order, no_order

    async def place_both_limit(
        self,
        yes_token_id: str,
        no_token_id: str,
        yes_price: float,
        no_price: float,
        shares: float,
        *,
        expiration: int = 0,
    ) -> tuple[Optional[PlacedOrder], Optional[PlacedOrder]]:
        """Place limit BUY orders for both YES and NO simultaneously.

        Args:
            yes_token_id: ERC-1155 token ID for YES.
            no_token_id: ERC-1155 token ID for NO.
            yes_price: Limit price for YES.
            no_price: Limit price for NO.
            shares: Number of tokens to buy per leg.
            expiration: Expiry unix timestamp (0 = GTC).

        Returns:
            Tuple of (yes_order, no_order); either may be ``None`` on failure.
        """
        yes_order, no_order = await asyncio.gather(
            self._clob.place_limit_order(yes_token_id, "BUY", yes_price, shares, expiration=expiration),
            self._clob.place_limit_order(no_token_id, "BUY", no_price, shares, expiration=expiration),
        )
        log.debug(
            "Limit orders placed | YES=%s@%.4f NO=%s@%.4f",
            yes_order.order_id if yes_order else "FAILED",
            yes_price,
            no_order.order_id if no_order else "FAILED",
            no_price,
        )
        return yes_order, no_order

    async def wait_for_fills(
        self,
        yes_order_id: str,
        no_order_id: str,
    ) -> bool:
        """Poll until both orders are filled or timeout expires.

        Args:
            yes_order_id: CLOB order ID for YES leg.
            no_order_id: CLOB order ID for NO leg.

        Returns:
            ``True`` if both orders filled before timeout.
        """
        deadline = asyncio.get_event_loop().time() + self._timeout
        while asyncio.get_event_loop().time() < deadline:
            yes_status, no_status = await asyncio.gather(
                self._clob.get_order_status(yes_order_id),
                self._clob.get_order_status(no_order_id),
            )
            yes_filled = _is_filled(yes_status)
            no_filled = _is_filled(no_status)
            log.debug(
                "Fill check | YES=%s(%s) NO=%s(%s)",
                yes_order_id[:8],
                "filled" if yes_filled else "pending",
                no_order_id[:8],
                "filled" if no_filled else "pending",
            )
            if yes_filled and no_filled:
                return True
            await asyncio.sleep(self._poll)
        log.warning("Fill timeout for orders %s / %s", yes_order_id[:8], no_order_id[:8])
        return False

    async def cancel_orders(
        self,
        yes_order_id: str,
        no_order_id: str,
    ) -> None:
        """Cancel both legs (best-effort; errors are logged, not raised).

        Args:
            yes_order_id: CLOB order ID for YES leg.
            no_order_id: CLOB order ID for NO leg.
        """
        results = await asyncio.gather(
            self._clob.cancel_order(yes_order_id),
            self._clob.cancel_order(no_order_id),
            return_exceptions=True,
        )
        for oid, result in zip([yes_order_id, no_order_id], results):
            if isinstance(result, Exception):
                log.warning("Cancel %s error: %s", oid[:8], result)
            elif result:
                log.debug("Cancelled order %s", oid[:8])


def _is_filled(status: Optional[dict]) -> bool:  # type: ignore[type-arg]
    """Determine if an order status dict indicates a complete fill.

    Args:
        status: Raw dict from :meth:`~scanner.clob_client.CLOBClient.get_order_status`.

    Returns:
        ``True`` if order is fully matched.
    """
    if status is None:
        return False
    s = str(status.get("status", "")).upper()
    size_matched = float(status.get("sizeMatched", 0) or 0)
    original_size = float(status.get("size", status.get("originalSize", 0)) or 0)
    # Accept explicit "MATCHED"/"FILLED" status or fully-matched size
    if s in ("MATCHED", "FILLED", "COMPLETED"):
        return True
    if original_size > 0 and abs(size_matched - original_size) < 1e-6:
        return True
    return False
