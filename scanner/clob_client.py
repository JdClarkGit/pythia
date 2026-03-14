"""Polymarket CLOB API client — order books, order placement, cancellation.

Authentication uses EIP-712 L1 (wallet-signed timestamp) for read/write access.
Order creation uses the Polymarket Order struct signed via eth_account.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import b64encode
from typing import Any, Optional

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from utils.logger import get_logger

log = get_logger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet

# CTFExchange EIP-712 domain
_DOMAIN = {
    "name": "Polymarket CTF Exchange",
    "version": "1",
    "chainId": CHAIN_ID,
    "verifyingContract": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
}

# EIP-712 typed data for Order
_ORDER_TYPES = {
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "taker", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "expiration", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "feeRateBps", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ]
}


class PriceLevel(BaseModel):
    """A single price level in an order book."""

    price: float
    size: float


class OrderBook(BaseModel):
    """Simplified order book for a single token."""

    token_id: str
    bids: list[PriceLevel] = Field(default_factory=list)
    asks: list[PriceLevel] = Field(default_factory=list)

    @property
    def best_ask(self) -> Optional[float]:
        """Best (lowest) ask price, or None if empty."""
        if not self.asks:
            return None
        return min(lv.price for lv in self.asks)

    @property
    def best_bid(self) -> Optional[float]:
        """Best (highest) bid price, or None if empty."""
        if not self.bids:
            return None
        return max(lv.price for lv in self.bids)

    @property
    def ask_depth_usdc(self) -> float:
        """Total USDC available at best ask level."""
        best = self.best_ask
        if best is None:
            return 0.0
        return sum(lv.price * lv.size for lv in self.asks if lv.price == best)


class PlacedOrder(BaseModel):
    """Response after successfully placing an order."""

    order_id: str
    status: str
    token_id: str
    side: str     # "BUY" | "SELL"
    price: float
    size: float


class CLOBClient:
    """Async client for the Polymarket CLOB API.

    Handles order-book reading (no auth) and authenticated order operations.

    Args:
        private_key: Hex-encoded wallet private key (for signing).
        api_key: Polymarket API key.
        api_secret: Polymarket API secret.
        api_passphrase: Polymarket API passphrase.
        base_url: Override CLOB base URL (tests).
        timeout: HTTP timeout seconds.
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None,
        base_url: str = CLOB_BASE,
        timeout: float = 15.0,
    ) -> None:
        self._pk = private_key
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._account = Account.from_key(private_key) if private_key else None

    async def __aenter__(self) -> "CLOBClient":
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

    # ------------------------------------------------------------------
    # Public / unauthenticated endpoints
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=False,
    )
    async def get_order_book(self, token_id: str) -> Optional[OrderBook]:
        """Fetch the current order book for a token.

        Args:
            token_id: ERC-1155 token ID (decimal string).

        Returns:
            :class:`OrderBook` or ``None`` on error.
        """
        assert self._client is not None
        try:
            resp = await self._client.get("/book", params={"token_id": token_id})
            resp.raise_for_status()
            return _parse_book(token_id, resp.json())
        except Exception as exc:  # noqa: BLE001
            log.debug("get_order_book(%s) error: %s", token_id, exc)
            return None

    async def get_mid_price(self, token_id: str) -> Optional[float]:
        """Return the mid-price for a token.

        Args:
            token_id: ERC-1155 token ID.

        Returns:
            Mid-price float or ``None``.
        """
        book = await self.get_order_book(token_id)
        if book is None:
            return None
        best_ask = book.best_ask
        best_bid = book.best_bid
        if best_ask is None or best_bid is None:
            return best_ask or best_bid
        return (best_ask + best_bid) / 2.0

    # ------------------------------------------------------------------
    # Authenticated order operations
    # ------------------------------------------------------------------

    async def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        *,
        expiration: int = 0,
    ) -> Optional[PlacedOrder]:
        """Place a limit (maker) order on the CLOB.

        Args:
            token_id: ERC-1155 token ID.
            side: ``"BUY"`` or ``"SELL"``.
            price: Limit price in [0, 1].
            size: Number of tokens to buy/sell.
            expiration: Unix timestamp for expiry (0 = GTC).

        Returns:
            :class:`PlacedOrder` on success, ``None`` on error.
        """
        if not self._account or not self._api_key:
            log.error("CLOBClient: credentials not configured")
            return None

        order_dict = self._build_order(token_id, side, price, size, expiration)
        signed = self._sign_order(order_dict)
        payload = {
            "order": signed,
            "owner": self._account.address,
            "orderType": "GTC",
        }

        headers = self._auth_headers("POST", "/order", json.dumps(payload))
        assert self._client is not None
        try:
            resp = await self._client.post(
                "/order",
                content=json.dumps(payload),
                headers={**headers, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            return PlacedOrder(
                order_id=data.get("orderID", data.get("order_id", "")),
                status=data.get("status", "placed"),
                token_id=token_id,
                side=side,
                price=price,
                size=size,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("place_limit_order failed: %s", exc)
            return None

    async def place_market_order(
        self,
        token_id: str,
        side: str,
        size: float,
    ) -> Optional[PlacedOrder]:
        """Place a market (taker) order on the CLOB.

        Args:
            token_id: ERC-1155 token ID.
            side: ``"BUY"`` or ``"SELL"``.
            size: Number of tokens to buy/sell.

        Returns:
            :class:`PlacedOrder` on success, ``None`` on error.
        """
        if not self._account or not self._api_key:
            log.error("CLOBClient: credentials not configured")
            return None

        book = await self.get_order_book(token_id)
        if book is None or book.best_ask is None:
            log.warning("No order book for token %s", token_id)
            return None

        # Use FOK (fill-or-kill) for market-style execution
        price = book.best_ask if side == "BUY" else (book.best_bid or 0.0)
        order_dict = self._build_order(token_id, side, price, size, 0, order_type="FOK")
        signed = self._sign_order(order_dict)
        payload = {
            "order": signed,
            "owner": self._account.address,
            "orderType": "FOK",
        }

        headers = self._auth_headers("POST", "/order", json.dumps(payload))
        assert self._client is not None
        try:
            resp = await self._client.post(
                "/order",
                content=json.dumps(payload),
                headers={**headers, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            return PlacedOrder(
                order_id=data.get("orderID", data.get("order_id", "")),
                status=data.get("status", "placed"),
                token_id=token_id,
                side=side,
                price=price,
                size=size,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("place_market_order failed: %s", exc)
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order.

        Args:
            order_id: CLOB order ID.

        Returns:
            ``True`` if successfully cancelled.
        """
        if not self._api_key:
            return False
        headers = self._auth_headers("DELETE", f"/order/{order_id}", "")
        assert self._client is not None
        try:
            resp = await self._client.delete(
                f"/order/{order_id}", headers=headers
            )
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("cancel_order(%s) failed: %s", order_id, exc)
            return False

    async def get_order_status(self, order_id: str) -> Optional[dict[str, Any]]:
        """Query the status of an order.

        Args:
            order_id: CLOB order ID.

        Returns:
            Raw status dict or ``None`` on error.
        """
        headers = self._auth_headers("GET", f"/order/{order_id}", "")
        assert self._client is not None
        try:
            resp = await self._client.get(f"/order/{order_id}", headers=headers)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        except Exception as exc:  # noqa: BLE001
            log.debug("get_order_status(%s): %s", order_id, exc)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        expiration: int,
        order_type: str = "GTC",
    ) -> dict[str, Any]:
        """Construct the Order typed-data message dict.

        Args:
            token_id: ERC-1155 token ID.
            side: ``"BUY"`` or ``"SELL"``.
            price: Limit price.
            size: Token quantity.
            expiration: Expiry unix timestamp (0 = GTC).
            order_type: ``"GTC"`` or ``"FOK"``.

        Returns:
            Dict matching the EIP-712 Order struct.
        """
        assert self._account is not None
        side_int = 0 if side == "BUY" else 1
        # Amounts in 6-decimal USDC units
        maker_amount = int(price * size * 1_000_000)  # USDC paid
        taker_amount = int(size * 1_000_000)          # tokens received
        salt = int(time.time() * 1000) % (2**256)
        return {
            "salt": salt,
            "maker": self._account.address,
            "signer": self._account.address,
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": int(token_id),
            "makerAmount": maker_amount,
            "takerAmount": taker_amount,
            "expiration": expiration,
            "nonce": 0,
            "feeRateBps": 0,
            "side": side_int,
            "signatureType": 0,  # EOA
        }

    def _sign_order(self, order: dict[str, Any]) -> dict[str, Any]:
        """EIP-712 sign an order dict and return it with the signature.

        Args:
            order: Order message dict from :meth:`_build_order`.

        Returns:
            Order dict augmented with ``"signature"`` field.
        """
        assert self._account is not None
        from eth_account.structured_data.hashing import hash_message  # type: ignore[import-untyped]

        structured = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                **_ORDER_TYPES,
            },
            "domain": _DOMAIN,
            "primaryType": "Order",
            "message": order,
        }
        try:
            # eth_account >= 0.9 supports sign_typed_data
            signed = self._account.sign_typed_data(
                domain_data=_DOMAIN,
                message_types=_ORDER_TYPES,
                message_data=order,
            )
            sig = signed.signature.hex()
        except Exception:  # noqa: BLE001
            # Fallback: sign the JSON-encoded message hash
            msg = encode_defunct(text=json.dumps(structured, sort_keys=True))
            signed = self._account.sign_message(msg)  # type: ignore[assignment]
            sig = signed.signature.hex()

        return {**order, "signature": sig}

    def _auth_headers(self, method: str, path: str, body: str) -> dict[str, str]:
        """Build HMAC-SHA256 auth headers for authenticated endpoints.

        Args:
            method: HTTP method (``"GET"``, ``"POST"``, etc.).
            path: Request path including query string.
            body: Request body string.

        Returns:
            Dict of HTTP headers.
        """
        if not (self._api_key and self._api_secret and self._api_passphrase):
            return {}
        ts = str(int(time.time() * 1000))
        msg = ts + method.upper() + path + (body or "")
        sig = hmac.new(
            self._api_secret.encode(),
            msg.encode(),
            hashlib.sha256,
        ).digest()
        sig_b64 = b64encode(sig).decode()
        return {
            "POLY_ADDRESS": self._account.address if self._account else "",
            "POLY_SIGNATURE": sig_b64,
            "POLY_TIMESTAMP": ts,
            "POLY_API_KEY": self._api_key,
            "POLY_PASSPHRASE": self._api_passphrase,
        }


def _parse_book(token_id: str, raw: dict[str, Any]) -> OrderBook:
    """Parse a raw CLOB /book response into an :class:`OrderBook`.

    Args:
        token_id: Token ID (used as identifier).
        raw: Raw API response dict.

    Returns:
        :class:`OrderBook` instance.
    """
    def _parse_levels(levels: list[Any]) -> list[PriceLevel]:
        result: list[PriceLevel] = []
        for lv in levels:
            if isinstance(lv, dict):
                p = float(lv.get("price", 0))
                s = float(lv.get("size", 0))
            elif isinstance(lv, (list, tuple)) and len(lv) >= 2:
                p, s = float(lv[0]), float(lv[1])
            else:
                continue
            if p > 0 and s > 0:
                result.append(PriceLevel(price=p, size=s))
        return sorted(result, key=lambda x: x.price)

    return OrderBook(
        token_id=token_id,
        bids=sorted(_parse_levels(raw.get("bids", [])), key=lambda x: x.price, reverse=True),
        asks=_parse_levels(raw.get("asks", [])),
    )
