"""Telegram push via Bot API (plain HTTP, no SDK)."""

from __future__ import annotations

import logging

import httpx

from .alert_engine import Alert
from .config import Settings

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._client = httpx.AsyncClient(timeout=10)

    @property
    def enabled(self) -> bool:
        return self.s.has_telegram

    async def close(self) -> None:
        await self._client.aclose()

    async def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            resp = await self._client.post(
                f"https://api.telegram.org/bot{self.s.tg_bot_token}/sendMessage",
                json={"chat_id": self.s.tg_chat_id, "text": text, "parse_mode": "HTML"},
            )
            if resp.status_code != 200:
                log.warning("telegram send failed: %s %s", resp.status_code, resp.text[:200])
                return False
            return True
        except httpx.HTTPError as exc:
            log.warning("telegram send error: %s", exc)
            return False

    async def send_alert(self, alert: Alert) -> bool:
        return await self.send(alert.message)
