"""OKX WebSocket subscriptions with auto-reconnect and ping/pong keepalive.

Public channels: tickers, candle1m, funding-rate.
Private channels (requires API key): account, positions.
Emits normalized events to the callback: (topic: str, data: dict).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import websockets

from . import okx_client as okx

log = logging.getLogger(__name__)

EventHandler = Callable[[str, dict], Awaitable[None]]


def _login_args(settings) -> list[str]:
    ts = okx.timestamp_iso()
    return [
        settings.api_key,
        settings.passphrase,
        ts,
        okx.sign(settings.secret_key, ts, "GET", "/users/self/verify"),
    ]


class _WsConnection:
    """One managed WebSocket connection (subscribe + reconnect loop)."""

    def __init__(self, name: str, url: str, subscriptions: list[dict],
                 on_event: EventHandler) -> None:
        self.name = name
        self.url = url
        self.subscriptions = subscriptions
        self.on_event = on_event
        self._backoff = 1.0

    async def run(self) -> None:
        while True:
            try:
                async with websockets.connect(self.url, ping_interval=None) as ws:
                    log.info("[%s] connected", self.name)
                    self._backoff = 1.0
                    if self.subscriptions:
                        await ws.send(json.dumps({"op": "subscribe", "args": self.subscriptions}))
                    async for raw in ws:
                        await self._handle(ws, raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("[%s] disconnected: %s — retrying in %.0fs",
                            self.name, exc, self._backoff)
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, 60)

    async def _handle(self, ws, raw: str | bytes) -> None:
        if raw == "pong":
            return
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            if raw == "ping":
                await ws.send("pong")
            return

        event = msg.get("event")
        if event in ("subscribe", "channel-conn-count"):
            return
        if event == "error":
            log.error("[%s] subscribe error: %s", self.name, msg)
            return
        if msg.get("arg") and msg.get("data"):
            channel = msg["arg"].get("channel", "")
            for item in msg["data"]:
                await self.on_event(channel, item)


class Datafeed:
    def __init__(self, settings, on_event: EventHandler) -> None:
        self.s = settings
        self.on_event = on_event
        self._tasks: list[asyncio.Task] = []

    def _public_subs(self) -> list[dict]:
        args: list[dict] = []
        for inst in self.s.watchlist:
            args.append({"channel": "tickers", "instId": inst})
            args.append({"channel": "candle1m", "instId": inst})
        for inst in self.s.swap_instruments:
            args.append({"channel": "funding-rate", "instId": inst})
        return args

    async def start(self) -> None:
        public_url = okx.WS_PUBLIC_DEMO if self.s.simulated else okx.WS_PUBLIC
        self._tasks.append(
            asyncio.create_task(
                _WsConnection("public", public_url, self._public_subs(), self.on_event).run()
            )
        )
        if self.s.has_private_access:
            private_url = okx.WS_PRIVATE_DEMO if self.s.simulated else okx.WS_PRIVATE

            async def _private_run() -> None:
                backoff = 1.0
                while True:
                    try:
                        async with websockets.connect(private_url, ping_interval=None) as ws:
                            log.info("[private] connected")
                            backoff = 1.0
                            await ws.send(json.dumps({"op": "login", "args": _login_args(self.s)}))
                            await ws.send(json.dumps({"op": "subscribe", "args": [
                                {"channel": "account"},
                                {"channel": "positions", "instType": "SWAP"},
                            ]}))
                            async for raw in ws:
                                await _handle_private(ws, raw, self.on_event)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.warning("[private] disconnected: %s — retrying in %.0fs",
                                    exc, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)

            self._tasks.append(asyncio.create_task(_private_run()))
        else:
            log.info("No OKX API key configured — running in public-data-only mode")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


async def _handle_private(ws, raw: str | bytes, on_event: EventHandler) -> None:
    if raw in ("pong", "ping"):
        if raw == "ping":
            await ws.send("pong")
        return
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if msg.get("event") in ("login", "subscribe", "error", "channel-conn-count"):
        if msg.get("event") == "error":
            log.error("[private] error: %s", msg)
        return
    if msg.get("arg") and msg.get("data"):
        channel = msg["arg"].get("channel", "")
        for item in msg["data"]:
            await on_event(channel, item)
