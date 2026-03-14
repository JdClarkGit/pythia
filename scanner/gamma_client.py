"""Polymarket Gamma API client for fetching market metadata."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from utils.logger import get_logger

log = get_logger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"


class TokenInfo(BaseModel):
    """YES or NO token descriptor returned by the Gamma API."""

    token_id: str
    outcome: str  # "Yes" or "No"
    winner: bool = False
    price: Optional[float] = None


class MarketInfo(BaseModel):
    """Subset of Gamma API market fields we care about."""

    condition_id: str
    question: str
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    active: bool = True
    closed: bool = False
    tokens: list[TokenInfo] = Field(default_factory=list)
    volume: float = 0.0
    liquidity: float = 0.0
    end_date: Optional[datetime] = None

    @property
    def yes_token_id(self) -> Optional[str]:
        """Token ID for the YES outcome, or None if unavailable."""
        for t in self.tokens:
            if t.outcome.lower() == "yes":
                return t.token_id
        return None

    @property
    def no_token_id(self) -> Optional[str]:
        """Token ID for the NO outcome, or None if unavailable."""
        for t in self.tokens:
            if t.outcome.lower() == "no":
                return t.token_id
        return None


class GammaClient:
    """Async HTTP client for the Polymarket Gamma REST API.

    Args:
        base_url: Override the default Gamma API base URL (useful for tests).
        timeout: HTTP timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = GAMMA_BASE,
        timeout: float = 15.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "GammaClient":
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=self._timeout,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=False,
    )
    async def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        """Low-level GET with retry.

        Args:
            path: API path relative to base URL.
            params: Optional query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            httpx.HTTPError: After all retries are exhausted.
        """
        assert self._client is not None, "Use as async context manager"
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_active_markets(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MarketInfo]:
        """Fetch a page of active, non-closed markets.

        Args:
            limit: Number of markets per page (max 100).
            offset: Pagination offset.

        Returns:
            List of :class:`MarketInfo` objects.
        """
        try:
            data = await self._get(
                "/markets",
                params={"active": "true", "closed": "false", "limit": limit, "offset": offset},
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Gamma /markets failed: %s", exc)
            return []

        markets: list[MarketInfo] = []
        for raw in data if isinstance(data, list) else data.get("markets", []):
            m = _parse_market(raw)
            if m is not None:
                markets.append(m)
        log.debug("Fetched %d active markets (offset=%d)", len(markets), offset)
        return markets

    async def get_all_active_markets(self, batch_size: int = 100) -> list[MarketInfo]:
        """Paginate through all active markets.

        Args:
            batch_size: Markets per API request.

        Returns:
            Full list of active :class:`MarketInfo` objects.
        """
        all_markets: list[MarketInfo] = []
        offset = 0
        while True:
            page = await self.get_active_markets(limit=batch_size, offset=offset)
            if not page:
                break
            all_markets.extend(page)
            if len(page) < batch_size:
                break
            offset += batch_size
            await asyncio.sleep(0.1)  # be polite
        log.info("Total active markets fetched: %d", len(all_markets))
        return all_markets

    async def get_market(self, condition_id: str) -> Optional[MarketInfo]:
        """Fetch a single market by condition ID.

        Args:
            condition_id: The market's Polymarket condition ID.

        Returns:
            :class:`MarketInfo` or ``None`` if not found / error.
        """
        try:
            data = await self._get(f"/markets/{condition_id}")
        except Exception as exc:  # noqa: BLE001
            log.error("Gamma /markets/%s failed: %s", condition_id, exc)
            return None
        return _parse_market(data)


def _parse_market(raw: dict[str, Any]) -> Optional[MarketInfo]:
    """Convert a raw Gamma API dict to a :class:`MarketInfo`.

    Args:
        raw: Raw dict from the API.

    Returns:
        Parsed :class:`MarketInfo` or ``None`` if required fields are missing.
    """
    try:
        cid = raw.get("conditionId") or raw.get("condition_id", "")
        if not cid:
            return None

        tokens_raw = raw.get("tokens", [])
        tokens: list[TokenInfo] = []
        for t in tokens_raw:
            tokens.append(
                TokenInfo(
                    token_id=str(t.get("token_id") or t.get("tokenId", "")),
                    outcome=t.get("outcome", ""),
                    winner=bool(t.get("winner", False)),
                    price=float(t["price"]) if t.get("price") is not None else None,
                )
            )

        tags_raw = raw.get("tags") or []
        if isinstance(tags_raw, list):
            tags = [str(x.get("label", x) if isinstance(x, dict) else x) for x in tags_raw]
        else:
            tags = []

        # Parse optional end date from camelCase or snake_case field
        end_date: Optional[datetime] = None
        raw_end = raw.get("endDate") or raw.get("end_date") or raw.get("endDateIso")
        if raw_end:
            try:
                end_date = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        return MarketInfo(
            condition_id=cid,
            question=str(raw.get("question", "")),
            category=str(raw.get("category", "")),
            tags=tags,
            active=bool(raw.get("active", True)),
            closed=bool(raw.get("closed", False)),
            tokens=tokens,
            volume=float(raw.get("volume", 0) or 0),
            liquidity=float(raw.get("liquidity", 0) or 0),
            end_date=end_date,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("Failed to parse market: %s — %s", raw.get("conditionId"), exc)
        return None
