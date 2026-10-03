"""Rule-based alert engine.

Rules (each with per-key cooldown):
  - price_move:    |% change| within window exceeds threshold
  - funding_rate:  |funding rate| exceeds threshold
  - position_change: position size opened / closed / changed
  - balance_change:  equity change beyond threshold (reported on account updates)
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .config import Settings


@dataclass
class Alert:
    type: str
    inst_id: str
    message: str
    severity: str = "info"
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type, "instId": self.inst_id, "message": self.message,
            "severity": self.severity, "ts": self.ts,
        }


class AlertEngine:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._prices: dict[str, deque[tuple[float, float]]] = {}
        self._last_funding: dict[str, float] = {}
        self._last_positions: dict[str, float] = {}
        self._last_alert_at: dict[tuple[str, str], float] = {}

    def _cooled_down(self, key: tuple[str, str], now: float) -> bool:
        last = self._last_alert_at.get(key)
        if last is not None and now - last < self.s.alert_cooldown_s:
            return False
        self._last_alert_at[key] = now
        return True

    # ---- rule inputs ----
    def on_ticker(self, inst_id: str, last_price: float, ts: float) -> list[Alert]:
        window = self.s.price_change_window_s
        dq = self._prices.setdefault(inst_id, deque())
        dq.append((ts, last_price))
        while dq and ts - dq[0][0] > window:
            dq.popleft()
        base = dq[0][1]
        alerts: list[Alert] = []
        if base > 0 and len(dq) >= 2:
            change_pct = (last_price - base) / base * 100
            if abs(change_pct) >= self.s.price_change_pct and self._cooled_down(
                ("price_move", inst_id), time.time()
            ):
                arrow = "🚀" if change_pct > 0 else "🌑"
                alerts.append(Alert(
                    type="price_move", inst_id=inst_id, severity="warn",
                    message=(f"{arrow} {inst_id} {change_pct:+.2f}% in "
                             f"{window // 60}min (now {last_price:g})"),
                ))
        return alerts

    def on_funding_rate(self, inst_id: str, rate_pct: float) -> list[Alert]:
        prev = self._last_funding.get(inst_id)
        self._last_funding[inst_id] = rate_pct
        if prev is None:
            return []  # first observation never alerts
        if abs(rate_pct) >= self.s.funding_rate_pct and self._cooled_down(
            ("funding_rate", inst_id), time.time()
        ):
            emoji = "🌕" if rate_pct > 0 else "🌘"
            return [Alert(
                type="funding_rate", inst_id=inst_id, severity="warn",
                message=f"{emoji} {inst_id} funding rate {rate_pct:+.4f}%",
            )]
        return []

    def on_positions(self, positions: list[dict]) -> list[Alert]:
        alerts: list[Alert] = []
        current: dict[str, float] = {}
        for p in positions:
            try:
                qty = float(p.get("pos", "0"))
            except (TypeError, ValueError):
                continue
            if qty:
                current[p["instId"]] = qty
        for inst_id, qty in current.items():
            prev = self._last_positions.get(inst_id)
            if prev is None or prev != qty:
                if prev is None or prev == 0:
                    rule = "position_open"
                    message = f"🔭 新开仓: {inst_id} → {qty:g}"
                else:
                    rule = "position_change"
                    message = f"🔭 持仓调整: {inst_id} {prev:g} → {qty:g}"
                if self._cooled_down((rule, inst_id), time.time()):
                    alerts.append(Alert(
                        type="position_change", inst_id=inst_id, message=message,
                    ))
        for inst_id, prev in self._last_positions.items():
            if inst_id not in current and prev:
                if self._cooled_down(("position_close", inst_id), time.time()):
                    alerts.append(Alert(
                        type="position_change", inst_id=inst_id,
                        message=f"🔭 平仓: {inst_id} (原 {prev:g})",
                    ))
        self._last_positions = current
        return alerts

    def on_balance(self, equity_usd: float) -> list[Alert]:
        prev = getattr(self, "_last_equity", None)
        self._last_equity = equity_usd
        if prev is None or prev <= 0:
            return []
        change_pct = (equity_usd - prev) / prev * 100
        if abs(change_pct) >= self.s.price_change_pct and self._cooled_down(
            ("balance_change", "*"), time.time()
        ):
            return [Alert(
                type="balance_change", inst_id="*", severity="warn",
                message=f"💰 权益变动 {change_pct:+.2f}% → {equity_usd:,.2f} USDT",
            )]
        return []
