"""Webhook alerting (Slack-compatible) with a no-op fallback when unconfigured."""

import asyncio
import json
from typing import Optional

import httpx

from utils.logger import get_logger

log = get_logger(__name__)


class Alerter:
    """Fire-and-forget webhook alerts (Slack / Discord / custom).

    Args:
        webhook_url: Full webhook URL.  When empty or ``None`` alerts are
                     silently dropped — useful for dry-run mode.
    """

    def __init__(self, webhook_url: Optional[str] = None) -> None:
        self._url = webhook_url or ""
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "Alerter":
        if self._url:
            self._client = httpx.AsyncClient(timeout=10)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()

    async def send(self, text: str, level: str = "info") -> None:
        """Send an alert message.

        Args:
            text: Human-readable message body.
            level: Severity tag (``"info"``, ``"warning"``, ``"error"``).
        """
        emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}.get(level, "📢")
        log.info("[bold]ALERT[/bold] %s %s", emoji, text)

        if not self._url or not self._client:
            return

        payload = {"text": f"{emoji} *arb-bot* | {text}"}
        try:
            resp = await self._client.post(
                self._url,
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("Alert delivery failed: %s", exc)

    def send_sync(self, text: str, level: str = "info") -> None:
        """Blocking wrapper around :meth:`send` for non-async contexts.

        Args:
            text: Human-readable message body.
            level: Severity tag.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.send(text, level))
            else:
                loop.run_until_complete(self.send(text, level))
        except Exception as exc:  # noqa: BLE001
            log.warning("send_sync failed: %s", exc)
