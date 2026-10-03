import base64
import hashlib
import hmac
import json

from astrolabe.config import Settings
from astrolabe.okx_client import OkxRestClient, sign


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
