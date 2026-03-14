"""Capital recycler: re-deploys freed USDC into new opportunities.

After each merge succeeds, the recycler checks if there are queued
opportunities and immediately allocates the freshly-returned capital,
maximising capital velocity.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Optional

from capital.manager import CapitalManager
from scanner.opportunity_detector import Opportunity
from utils.logger import get_logger

log = get_logger(__name__)


class CapitalRecycler:
    """Maintains an ordered queue of opportunities and triggers re-deployment.

    Args:
        capital: :class:`~capital.manager.CapitalManager` to query free capital.
        max_queue_size: Maximum queued opportunities (oldest discarded first).
        min_usdc_to_deploy: Do not deploy if free capital below this threshold.
    """

    def __init__(
        self,
        capital: CapitalManager,
        *,
        max_queue_size: int = 50,
        min_usdc_to_deploy: float = 5.0,
    ) -> None:
        self._capital = capital
        self._queue: deque[Opportunity] = deque(maxlen=max_queue_size)
        self._min_deploy = min_usdc_to_deploy
        self._callbacks: list = []  # list[Callable[[Opportunity], Coroutine]]

    def enqueue(self, opportunities: list[Opportunity]) -> None:
        """Add fresh opportunities to the queue.

        Older, lower-priority items are discarded when the queue is full.

        Args:
            opportunities: List sorted by descending net profit.
        """
        for opp in opportunities:
            self._queue.append(opp)
        log.debug("Queue size: %d (added %d)", len(self._queue), len(opportunities))

    def register_executor(self, callback: object) -> None:
        """Register an async callback to invoke with the next opportunity.

        The callback signature: ``async def execute(opp: Opportunity) -> None``.

        Args:
            callback: Awaitable callable.
        """
        self._callbacks.append(callback)

    async def recycle(self) -> int:
        """Attempt to deploy free capital into queued opportunities.

        Called after each successful merge to consume the returned USDC.

        Returns:
            Number of opportunities dispatched in this cycle.
        """
        dispatched = 0
        while self._queue and self._capital.free_usdc >= self._min_deploy:
            opp = self._queue.popleft()
            for cb in self._callbacks:
                try:
                    asyncio.create_task(cb(opp))
                    dispatched += 1
                except Exception as exc:  # noqa: BLE001
                    log.error("Recycler callback error: %s", exc)
        if dispatched:
            log.info("Recycled capital into %d new opportunities", dispatched)
        return dispatched

    def queue_depth(self) -> int:
        """Return the number of queued opportunities.

        Returns:
            Current queue depth.
        """
        return len(self._queue)

    def peek(self) -> Optional[Opportunity]:
        """Return the next queued opportunity without removing it.

        Returns:
            Next :class:`~scanner.opportunity_detector.Opportunity` or ``None``.
        """
        return self._queue[0] if self._queue else None
