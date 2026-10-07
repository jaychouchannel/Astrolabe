import base64
import hashlib
import hmac
import json

import pytest

from astrolabe.config import Settings
from astrolabe.okx_client import OkxError, OkxRestClient, calc_contracts, sign


def test_sign_matches_hmac_spec():
    secret = "22582BD0CFF14C41EDBF1AB98506286D"
    ts = "2020-12-08T09:08:57.715Z"
    expected = base64.b64encode(
        hmac.new(secret.encode(), f"{ts}GET/users/self/verify".encode(), hashlib.sha256).digest()
    ).decode()
    assert sign(secret, ts, "GET", "/users/self/verify") == expected


def test_sign_ignores_method_case():
    a = sign("s", "ts", "get", "/x", "b")
    b = sign("s", "ts", "GET", "/x", "b")
    assert a == b


def test_settings_demo_defaults():
    s = Settings()
    assert s.simulated is True
    assert s.has_private_access is False
    assert s.watchlist == s.spot_instruments + s.swap_instruments


def test_rest_client_headers_public_vs_private():
    s = Settings(api_key="k", secret_key="sec", passphrase="p", simulated=True)
    client = OkxRestClient(s)
    headers = client._headers("GET", "/api/v5/market/tickers")
    assert headers["OK-ACCESS-KEY"] == "k"
    assert headers["x-simulated-trading"] == "1"

    pub = OkxRestClient(Settings(simulated=True))
    pub_headers = pub._headers("GET", "/api/v5/market/tickers")
    assert "OK-ACCESS-KEY" not in pub_headers
    assert pub_headers["x-simulated-trading"] == "1"


def test_calc_contracts_basic():
    # 名义预算 300U (100x3), 一张 120000*0.01=1200U -> 0 张, 取 minSz=1
    assert calc_contracts(100, 3, 120000.0, 0.01, 1) == 1
    # 名义预算 3000U -> 2 张
    assert calc_contracts(1000, 3, 120000.0, 0.01, 1) == 2
    # 非法价格回退最小张数
    assert calc_contracts(100, 3, 0, 0.01, 1) == 1


@pytest.mark.asyncio
async def test_place_order_gated_by_trading_enabled():
    client = OkxRestClient(Settings())
    with pytest.raises(OkxError, match="OKX_TRADING_ENABLED"):
        await client.place_order("BTC-USDT-SWAP", "buy", None, 1)


def test_place_order_payload_shapes():
    s = Settings(api_key="k", secret_key="sec", passphrase="p",
                 simulated=True, trading_enabled=True)
    client = OkxRestClient(s)
    # net 模式: 无 posSide
    body = client._order_body("BTC-USDT-SWAP", "buy", None, 2)
    assert body == {"instId": "BTC-USDT-SWAP", "tdMode": "isolated",
                    "side": "buy", "ordType": "market", "sz": "2"}
    # long/short 模式: 平多用 sell+long
    body = client._order_body("BTC-USDT-SWAP", "sell", "long", 2)
    assert body["posSide"] == "long" and body["side"] == "sell"
