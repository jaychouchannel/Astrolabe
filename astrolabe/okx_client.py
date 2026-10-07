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


def calc_contracts(margin_usdt: float, lever: int, price: float,
                   ct_val: float, min_sz: float) -> int:
    """Swap contracts for a margin budget at leverage; never below minSz.

    One contract is worth price * ctVal; a small budget can't fill even one
    contract, in which case we still trade minSz and record the real notional.
    """
    notional = margin_usdt * lever
    if price <= 0 or ct_val <= 0:
        return int(min_sz)
    return max(int(min_sz), int(notional / (price * ct_val)))


class OkxRestClient:
    def __init__(self, settings) -> None:
        self.s = settings
        self._client = httpx.AsyncClient(base_url=REST_BASE, timeout=15)
        self._instruments: dict[str, dict] = {}

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

    def _order_body(self, inst_id: str, side: str, pos_side: str | None,
                    sz: int, td_mode: str = "isolated") -> dict:
        body: dict = {"instId": inst_id, "tdMode": td_mode, "side": side,
                      "ordType": "market", "sz": str(sz)}
        if pos_side:
            body["posSide"] = pos_side
        return body

    async def instruments(self, inst_id: str) -> dict:
        """Instrument spec (ctVal/minSz), cached for the process lifetime."""
        if inst_id in self._instruments:
            return self._instruments[inst_id]
        data = await self._request(
            "GET", "/api/v5/public/instruments",
            {"instType": "SWAP", "instId": inst_id},
        )
        self._instruments[inst_id] = data[0]
        return data[0]

    async def account_config(self) -> dict:
        data = await self._request("GET", "/api/v5/account/config")
        return data[0]

    async def set_leverage(self, inst_id: str, lever: str,
                           mgn_mode: str = "isolated",
                           pos_side: str | None = None) -> dict:
        """Set leverage. posSide is required by OKX v5 in long/short mode
        combined with isolated margin."""
        body: dict = {"instId": inst_id, "lever": lever, "mgnMode": mgn_mode}
        if pos_side:
            body["posSide"] = pos_side
        data = await self._request(
            "POST", "/api/v5/account/set-leverage",
            json_body=body,
        )
        return data[0]

    async def place_order(self, inst_id: str, side: str, pos_side_or_sz: str | None,
                          sz: int | None = None, td_mode: str = "isolated") -> dict:
        """Market order. Gate: raises unless OKX_TRADING_ENABLED=1.

        Supports two call shapes:
        - strategy swap mode: (inst_id, side, pos_side, sz, td_mode)
        - generic order mode: (inst_id, side, sz_str)
        """
        if not self.s.trading_enabled:
            raise OkxError("trading disabled: set OKX_TRADING_ENABLED=1")
        if sz is None:
            sz_str = str(pos_side_or_sz)
            if inst_id.endswith("-SWAP"):
                body = {"instId": inst_id, "tdMode": "cross", "side": side,
                        "ordType": "market", "sz": sz_str}
            else:
                body = {"instId": inst_id, "tdMode": "cash", "side": side,
                        "ordType": "market", "sz": sz_str, "tgtCcy": "base_ccy"}
        else:
            body = self._order_body(inst_id, side, pos_side_or_sz, sz, td_mode)
        result = await self._request("POST", "/api/v5/trade/order", json_body=body)
        entry = result[0] if result else {}
        if str(entry.get("sCode", "0")) != "0":
            raise OkxError(f"order rejected {entry.get('sCode')}: {entry.get('sMsg')}")
        return entry
