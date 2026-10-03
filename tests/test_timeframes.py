from astrolabe.config import Settings
from astrolabe.datafeed import Datafeed


def test_public_subs_include_all_timeframes():
    s = Settings(spot_instruments=["BTC-USDT"], swap_instruments=[])
    subs = Datafeed(s, lambda *a: None)._public_subs()
    candle_channels = [a["channel"] for a in subs if a["channel"].startswith("candle")]
    assert set(candle_channels) == {
        "candle1m", "candle5m", "candle15m", "candle1H",
        "candle4H", "candle1D", "candle1W", "candle1M",
    }
    assert all(a["instId"] == "BTC-USDT" for a in subs if a["channel"].startswith("candle"))


def test_timeframes_configurable():
    s = Settings(spot_instruments=["BTC-USDT"], swap_instruments=[],
                 chart_timeframes=["1D"])
    subs = Datafeed(s, lambda *a: None)._public_subs()
    assert [a["channel"] for a in subs if a["channel"].startswith("candle")] == ["candle1D"]
