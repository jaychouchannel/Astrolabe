import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from astrolabe.config import Settings
from astrolabe.okx_client import REST_BASE, OkxRestClient, sign


def test_trading_disabled_by_default():
    assert Settings().trading_enabled is False


def test_trading_enabled_via_env(monkeypatch):
    monkeypatch.setenv("OKX_TRADING_ENABLED", "1")
    assert Settings.__dataclass_fields__  # sanity
    from astrolabe.config import load_settings
    s = load_settings()
    assert s.trading_enabled is True


def _client_with_handler(handler):
    s = Settings(api_key="k", secret_key="sec", passphrase="p",
                 simulated=True, trading_enabled=True)
    c = OkxRestClient(s)
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=REST_BASE
    )
    return c


def test_place_order_spot_signs_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        captured["ts"] = request.headers["OK-ACCESS-TIMESTAMP"]
        captured["sign"] = request.headers["OK-ACCESS-SIGN"]
        return httpx.Response(200, json={
            "code": "0",
            "data": [{"ordId": "123", "sCode": "0", "sMsg": "Order placed"}],
        })

    c = _client_with_handler(handler)
    result = asyncio.run(c.place_order("BTC-USDT", "buy", "0.001"))
    asyncio.run(c._client.aclose())

    assert result["ordId"] == "123"
    body = json.loads(captured["body"])
    assert body["tdMode"] == "cash"
    assert body["tgtCcy"] == "base_ccy"
    assert body["side"] == "buy" and body["sz"] == "0.001"
    # signature must cover method + path + JSON body
    expected = sign("sec", captured["ts"], "POST", captured["path"], captured["body"])
    assert captured["sign"] == expected


def test_place_order_swap_uses_cross_mode():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        captured["ts"] = request.headers["OK-ACCESS-TIMESTAMP"]
        captured["sign"] = request.headers["OK-ACCESS-SIGN"]
        return httpx.Response(200, json={
            "code": "0",
            "data": [{"ordId": "456", "sCode": "0", "sMsg": "Order placed"}],
        })

    c = _client_with_handler(handler)
    result = asyncio.run(c.place_order("BTC-USDT-SWAP", "sell", "1"))
    asyncio.run(c._client.aclose())

    assert result["ordId"] == "456"
    assert captured["body"]["tdMode"] == "cross"
    assert "tgtCcy" not in captured["body"]
    # signature verifies against timestamp + POST + path + body
    expected = sign("sec", captured["ts"], "POST", "/api/v5/trade/order",
                    json.dumps(captured["body"]))
    assert captured["sign"] == expected


def test_place_order_rejection_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "code": "0",
            "data": [{"ordId": "", "sCode": "51000", "sMsg": "Insufficient balance"}],
        })

    c = _client_with_handler(handler)
    try:
        asyncio.run(c.place_order("BTC-USDT", "buy", "999"))
        assert False, "should have raised"
    except Exception as exc:
        assert "Insufficient balance" in str(exc)
    finally:
        asyncio.run(c._client.aclose())


def test_api_order_gating(monkeypatch):
    from astrolabe import app as appmod

    client = TestClient(appmod.app)

    # 1) trading disabled -> 403
    monkeypatch.setattr(appmod.settings, "trading_enabled", False)
    resp = client.post("/api/order", json={"instId": "BTC-USDT", "side": "buy", "sz": "0.001"})
    assert resp.status_code == 403
    assert "OKX_TRADING_ENABLED" in resp.json()["error"]

    # 2) trading enabled but no API key -> 403
    monkeypatch.setattr(appmod.settings, "trading_enabled", True)
    resp = client.post("/api/order", json={"instId": "BTC-USDT", "side": "buy", "sz": "0.001"})
    assert resp.status_code == 403
    assert "API key" in resp.json()["error"]

    # 3) enabled + key present (monkeypatched) -> succeeds
    monkeypatch.setattr(type(appmod.settings), "has_private_access", lambda self: True)

    async def fake_place(inst_id, side, sz):
        assert side == "buy"
        return {"ordId": "999"}

    monkeypatch.setattr(appmod.rest, "place_order", fake_place)
    monkeypatch.setattr(appmod.settings, "simulated", True)
    monkeypatch.setattr(appmod.rest, "positions", lambda: asyncio.sleep(0, []))
    resp = client.post("/api/order", json={"instId": "BTC-USDT", "side": "buy", "sz": "0.001"})
    assert resp.status_code == 200
    assert resp.json()["ordId"] == "999"

    # 4) invalid side -> 400
    resp = client.post("/api/order", json={"instId": "BTC-USDT", "side": "hop", "sz": "1"})
    assert resp.status_code == 400
