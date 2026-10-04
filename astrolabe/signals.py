"""Trend/volatility signal engine for the candlestick chart.

Computes MA5/MA20 cross, RSI(14) and Bollinger bands (20, ±2σ) from a
candlestick series and combines them into a single read-only suggestion
(buy_in / sell_out / hold) used as an intraday 做T reference.

Pure computation — no IO, no order logic, no alerts.
"""

from __future__ import annotations

from typing import Any

MA_FAST, MA_SLOW, BOLL_N, BOLL_K = 5, 20, 20, 2.0
RSI_N = 14
RSI_OVERSOLD, RSI_OVERBOUGHT = 30.0, 70.0
PCTB_LOW, PCTB_HIGH = 0.2, 0.8
CROSS_LOOKBACK = 30

LABELS = {
    "buy_in": "低位区间 · 可考虑接回做T",
    "sell_out": "高位区间 · 可考虑T出",
    "hold": "观望",
}


def _sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    window = sum(values[:n])
    out[n - 1] = window / n
    for i in range(n, len(values)):
        window += values[i] - values[i - n]
        out[i] = window / n
    return out


def _rsi(values: list[float], n: int = RSI_N) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        delta = values[i] - values[i - 1]
        gain += max(delta, 0.0)
        loss += max(-delta, 0.0)
    avg_gain, avg_loss = gain / n, loss / n
    out[n] = _rsi_value(avg_gain, avg_loss)
    for i in range(n + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(delta, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-delta, 0.0)) / n
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 50.0 if avg_gain == 0 else 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _boll(values: list[float], n: int = BOLL_N, k: float = BOLL_K) -> tuple[
        list[float | None], list[float | None], list[float | None]]:
    mid = _sma(values, n)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1:i + 1]
        sd = (sum((v - mid[i]) ** 2 for v in window) / n) ** 0.5
        upper[i] = mid[i] + k * sd
        lower[i] = mid[i] - k * sd
    return upper, mid, lower


def _last_cross(ma_fast: list[float | None], ma_slow: list[float | None],
                ts: list[int]) -> dict[str, Any] | None:
    prev_diff: float | None = None
    for i in range(len(ma_fast)):
        if ma_fast[i] is None or ma_slow[i] is None:
            continue
        diff = ma_fast[i] - ma_slow[i]
        if prev_diff is not None and diff != 0 and (diff > 0) != (prev_diff > 0):
            if len(ma_fast) - i <= CROSS_LOOKBACK:
                kind = "golden" if diff > 0 else "death"
                return {"type": kind, "index": i, "ts": ts[i]}
        prev_diff = diff
    return None


def compute_signals(candles: list[list]) -> dict[str, Any]:
    """candles: OKX format, newest-first rows [ts, o, h, l, c, ...]."""
    rows = [r for r in candles if len(r) >= 5]
    rows.reverse()  # chronological
    ts = [int(r[0]) for r in rows]
    closes = [float(r[4]) for r in rows]

    ma5 = _sma(closes, MA_FAST)
    ma20 = _sma(closes, MA_SLOW)
    rsi = _rsi(closes)
    boll_upper, boll_mid, boll_lower = _boll(closes)

    result: dict[str, Any] = {
        "ma5": None, "ma20": None, "rsi": None,
        "boll": None, "last_cross": None,
        "action": "hold", "label": LABELS["hold"], "reason": "",
        "series": {"ts": ts, "ma5": ma5, "ma20": ma20,
                   "boll_upper": boll_upper, "boll_mid": boll_mid,
                   "boll_lower": boll_lower},
    }
    if len(closes) < MA_SLOW + RSI_N:
        result["label"] = "数据不足 · 观望"
        result["reason"] = f"K 线不足 {MA_SLOW + RSI_N} 根,指标不可靠"
        return result

    i = len(closes) - 1
    last = closes[i]
    rsi_v = rsi[i]
    up, mid, low = boll_upper[i], boll_mid[i], boll_lower[i]
    band = up - low
    pctb = (last - low) / band if band > 0 else 0.5
    cross = _last_cross(ma5, ma20, ts)

    result.update({
        "ma5": ma5[i], "ma20": ma20[i], "rsi": rsi_v,
        "boll": {"upper": up, "mid": mid, "lower": low, "pctb": pctb},
        "last_cross": cross,
    })

    near_low, near_high = pctb <= PCTB_LOW, pctb >= PCTB_HIGH
    oversold, overbought = rsi_v <= RSI_OVERSOLD, rsi_v >= RSI_OVERBOUGHT

    cross_note = ""
    if cross:
        cross_note = f"MA{MA_FAST} 近期{'金叉' if cross['type'] == 'golden' else '死叉'}，"

    if near_low and oversold:
        result["action"] = "buy_in"
        result["label"] = LABELS["buy_in"]
        result["reason"] = (f"{cross_note}RSI {rsi_v:.0f} 超卖，价格贴近布林下轨 "
                            f"({low:.4g})，%B {pctb:.0%}")
    elif near_high and overbought:
        result["action"] = "sell_out"
        result["label"] = LABELS["sell_out"]
        result["reason"] = (f"{cross_note}RSI {rsi_v:.0f} 超买，价格贴近布林上轨 "
                            f"({up:.4g})，%B {pctb:.0%}")
    else:
        result["reason"] = (f"RSI {rsi_v:.0f}，%B {pctb:.0%}，"
                            + ("有" + ("金叉" if cross and cross["type"] == "golden" else "死叉")
                               + "参考" if cross else "无明显交叉信号"))
    return result
