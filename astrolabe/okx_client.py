"""OKX REST + WebSocket endpoint wrapper.

REST signing: OKX requires headers
    OK-ACCESS-KEY / OK-ACCESS-SIGN / OK-ACCESS-TIMESTAMP / OK-ACCESS-PASSPHRASE
where SIGN = Base64(HMAC-SHA256(timestamp + method + requestPath + body, secret)).
Demo trading adds header ``x-simulated-trading: 1``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

REST_BASE = "https://www.okx.com"
WS_PUBLIC = "wss://ws.okx.com:8443/ws/v5/public"
WS_PRIVATE = "wss://ws.okx.com:8443/ws/v5/private"
# demo-trading endpoints
WS_PUBLIC_DEMO = "wss://wspap.okx.com:8443/ws/v5/public"
WS_PRIVATE_DEMO = "wss://wspap.okx.com:8443/ws/v5/private"


def sign(secret_key: str, timestamp: str, method: str, request_path: str, body: str = "") -> str:
    message = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def timestamp_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class OkxError(RuntimeError):
    pass


class OkxRestClient:
    def __init__(self, settings) -> None:
        self.s = settings
        self._client = httpx.AsyncClient(base_url=REST_BASE, timeout=15)

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self, method: str, request_path: str, body: str = "") -> dict[str, str]:
        ts = timestamp_iso()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.s.has_private_access:
            headers.update(
                {
                    "OK-ACCESS-KEY": self.s.api_key,
                    "OK-ACCESS-SIGN": sign(self.s.secret_key, ts, method, request_path, body),
                    "OK-ACCESS-TIMESTAMP": ts,
                    "OK-ACCESS-PASSPHRASE": self.s.passphrase,
                }
            )
        if self.s.simulated:
            headers["x-simulated-trading"] = "1"
        return headers

    async def _request(self, method: str, path: str, params: dict | None = None,
                       json_body: dict | None = None) -> Any:
        request_path = path
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            request_path = f"{path}?{query}"
        body = json.dumps(json_body) if json_body is not None else ""
        resp = await self._client.request(
            method, request_path,
            content=body if json_body is not None else None,
            headers=self._headers(method, request_path, body),
        )
        if resp.status_code == 429:
            raise OkxError("rate limited (429)")
        data = resp.json()
        if data.get("code") != "0":
            raise OkxError(f"OKX error {data.get('code')}: {data.get('msg')}")
        return data.get("data")

    # ---- public ----
    async def tickers(self, inst_type: str) -> list[dict]:
        return await self._request("GET", "/api/v5/market/tickers", {"instType": inst_type})

    async def ticker(self, inst_id: str) -> dict:
        data = await self._request("GET", "/api/v5/market/ticker", {"instId": inst_id})
        return data[0]

    async def candles(self, inst_id: str, bar: str = "1m", limit: int = 200) -> list[list]:
        return await self._request(
            "GET", "/api/v5/market/candles",
            {"instId": inst_id, "bar": bar, "limit": str(limit)},
        )

    async def funding_rate(self, inst_id: str) -> dict:
        data = await self._request("GET", "/api/v5/public/funding-rate", {"instId": inst_id})
        return data[0]

    # ---- private ----
    async def balance(self, ccy: str = "USDT") -> dict:
        data = await self._request("GET", "/api/v5/account/balance", {"ccy": ccy})
        details = data[0].get("details", []) if data else []
        return next((d for d in details if d.get("ccy") == ccy), {})

    async def positions(self) -> list[dict]:
        return await self._request("GET", "/api/v5/account/positions", {"instType": "SWAP"})

    # ---- trading (guarded by settings.trading_enabled at the API layer) ----
    async def place_order(self, inst_id: str, side: str, sz: str) -> dict:
        """Market order. Spot: sz in base currency (e.g. 0.01 BTC).
        SWAP: sz in contracts (张)."""
        if inst_id.endswith("-SWAP"):
            td_mode = "cross"
        else:
            td_mode = "cash"
        body = {"instId": inst_id, "tdMode": td_mode, "side": side,
                "ordType": "market", "sz": sz}
        if inst_id.endswith("-SWAP") is False:
            body["tgtCcy"] = "base_ccy"
        result = await self._request("POST", "/api/v5/trade/order", json_body=body)
        entry = result[0] if result else {}
        if str(entry.get("sCode", "0")) != "0":
            raise OkxError(f"order rejected: {entry.get('sMsg')}")
        return entry
