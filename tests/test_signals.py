"""Tests for the chart signal engine (astrolabe.signals)."""



from astrolabe.signals import compute_signals


def make_candles(closes: list[float], start_ts: int = 1_700_000_000_000,
                 step_ms: int = 60_000) -> list[list]:
    """OKX candle rows (newest-first) built from a chronological close list."""
    rows = []
    for i, close in enumerate(closes):
        ts = start_ts + i * step_ms
        rows.append([str(ts), str(close), str(close), str(close), str(close),
                     "10", "0", "0", "1"])
    rows.reverse()
    return rows


def test_uptrend_is_sell_out():
    closes = [100 + i for i in range(60)]
    result = compute_signals(make_candles(closes))
    assert result["action"] == "sell_out"
    assert result["rsi"] == 100.0
    assert result["boll"]["pctb"] >= 0.8


def test_downtrend_is_buy_in():
    closes = [200 - i for i in range(60)]
    result = compute_signals(make_candles(closes))
    assert result["action"] == "buy_in"
    assert result["rsi"] == 0.0
    assert result["boll"]["pctb"] <= 0.2


def test_v_shape_detects_golden_cross():
    closes = [200 - i for i in range(50)] + [151 + i for i in range(1, 25)]
    result = compute_signals(make_candles(closes))
    assert result["last_cross"] is not None
    assert result["last_cross"]["type"] == "golden"
    # series arrays align with the timestamp axis
    n = len(result["series"]["ts"])
    for key in ("ma5", "ma20", "boll_upper", "boll_mid", "boll_lower"):
        assert len(result["series"][key]) == n


def test_insufficient_data_is_hold_without_error():
    closes = [100 + (i % 3) for i in range(10)]
    result = compute_signals(make_candles(closes))
    assert result["action"] == "hold"
    assert "数据不足" in result["label"]
    assert result["ma5"] is None and result["rsi"] is None


def test_flat_prices_do_not_crash():
    closes = [100.0] * 60
    result = compute_signals(make_candles(closes))
    assert result["action"] == "hold"
    assert result["boll"]["pctb"] == 0.5
    assert result["rsi"] == 50.0  # zero volatility → neutral, not overbought
