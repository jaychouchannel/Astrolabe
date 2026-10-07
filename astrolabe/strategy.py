"""观星执行 — auto-trading strategy runner.

Turns the chart signal (MA/RSI/Bollinger 做T reference) into market orders on
one USDT swap instrument. Decision logic is separated from IO so the state
machine is testable without network.

Gates: an order can only happen when settings.trading_enabled is on (enforced
again inside okx_client.place_order); the runner itself is only started when
STRATEGY_ENABLED=1 (app.py additionally requires STRATEGY_ALLOW_LIVE=1 on live).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from .okx_client import OkxError, OkxRestClient, calc_contracts
from .signals import compute_signals
from .storage import Storage

log = logging.getLogger(__name__)

TARGET_FROM_ACTION = {"buy_in": "long", "sell_out": "short", "hold": None}


def plan_actions(current: str | None, target: str | None) -> list[dict]:
    """Ordered actions to move from the current position to the target.

    current/target: "long" | "short" | None (flat). A reversal is
    close-then-open; identical sides produce no actions.
    """
    if current == target:
        return []
    actions: list[dict] = []
    if current is not None:
        actions.append({"action": "close", "direction": current})
    if target is not None:
        actions.append({"action": "open", "direction": target})
    return actions


class StrategyRunner:
    def __init__(self, settings, rest: OkxRestClient, storage: Storage,
                 on_trade: Callable[[dict], Awaitable[None]] | None = None) -> None:
        self.s = settings
        self.rest = rest
        self.storage = storage
        self.on_trade = on_trade
        self.side: str | None = None
        self.sz = 0
        self.entry_px: float | None = None
        self.realized_pnl = 0.0
        self.last_signal: dict[str, Any] | None = None
        self.last_trade_ts = 0.0
        self.pos_mode = "net_mode"
        self.ct_val = 0.01
        self.min_sz = 1.0
        self._task: asyncio.Task | None = None
        self._stopping = False

    # ---- lifecycle ----
    async def start(self) -> None:
        inst = await self.rest.instruments(self.s.strategy_inst)
        self.ct_val = float(inst.get("ctVal", 0.01))
        self.min_sz = float(inst.get("minSz", 1))
        if self.s.has_private_access:
            try:
                self.pos_mode = (await self.rest.account_config()).get(
                    "posMode", "net_mode")
                await self.rest.set_leverage(
                    self.s.strategy_inst, str(self.s.strategy_lever))
            except OkxError as exc:
                log.warning("account setup degraded, assuming net mode: %s", exc)
        await self._reconcile()
        self._stopping = False
        self._task = asyncio.create_task(self._run())
        log.info("strategy runner on %s (%s, posMode=%s, ctVal=%s)",
                 self.s.strategy_inst, self.s.strategy_bar, self.pos_mode, self.ct_val)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await self.step()
            except Exception:
                log.exception("strategy step failed")
            await asyncio.sleep(self.s.strategy_poll_s)

    # ---- decision ----
    async def step(self) -> None:
        candles = await self.rest.candles(
            self.s.strategy_inst, self.s.strategy_bar, limit=100)
        await self.on_signal(compute_signals(candles))

    async def on_signal(self, signal: dict) -> None:
        self.last_signal = signal
        if time.time() - self.last_trade_ts < self.s.strategy_cooldown_s:
            return
        target = TARGET_FROM_ACTION.get(signal.get("action", "hold"))
        actions = plan_actions(self.side, target)
        if not actions:
            return
        price = float((await self.rest.ticker(self.s.strategy_inst))["last"])
        for act in actions:
            await self._execute(act, signal, price)

    async def _execute(self, act: dict, signal: dict, price: float) -> None:
        direction = act["direction"]
        side = "buy" if (direction == "long") == (act["action"] == "open") else "sell"
        if act["action"] == "open":
            sz = calc_contracts(self.s.strategy_size_usdt, self.s.strategy_lever,
                                price, self.ct_val, self.min_sz)
        else:
            sz = self.sz
        try:
            result = await self.rest.place_order(
                self.s.strategy_inst, side,
                pos_side=None if self.pos_mode == "net_mode" else direction,
                sz=sz,
            )
        except OkxError as exc:
            log.warning("order failed (%s %s): %s", act["action"], direction, exc)
            self.storage.record_trade({
                "ts": time.time(), "inst_id": self.s.strategy_inst,
                "action": "error", "direction": direction, "sz": sz,
                "px": price, "usdt_notional": 0.0,
                "signal": signal.get("action", ""), "pnl": 0.0})
            if self.on_trade:
                await self.on_trade({"ts": time.time(), "instId": self.s.strategy_inst,
                                     "action": "error", "direction": direction,
                                     "sz": sz, "px": price, "usdtNotional": 0.0,
                                     "signal": signal.get("action", ""), "pnl": 0.0})
            return
        px = float(result.get("avgPx") or price)
        notional = px * sz * self.ct_val
        pnl = 0.0
        if act["action"] == "close" and self.entry_px:
            pnl = (px - self.entry_px) * sz * self.ct_val * (
                1 if self.side == "long" else -1)
            self.realized_pnl += pnl
        trade = {"ts": time.time(), "inst_id": self.s.strategy_inst,
                 "action": act["action"], "direction": direction, "sz": sz,
                 "px": px, "usdt_notional": notional,
                 "signal": signal.get("action", ""), "pnl": pnl}
        self.storage.record_trade(trade)
        if act["action"] == "close":
            self.side, self.sz, self.entry_px = None, 0, None
        else:
            self.side, self.sz, self.entry_px = direction, sz, px
        self.last_trade_ts = time.time()
        log.info("STRATEGY %s %s %d ct @ %.4g notional=%.2f pnl=%.2f",
                 act["action"], direction, sz, px, notional, pnl)
        if self.on_trade:
            await self.on_trade({"ts": trade["ts"], "instId": trade["inst_id"],
                                 "action": trade["action"],
                                 "direction": trade["direction"], "sz": trade["sz"],
                                 "px": trade["px"],
                                 "usdtNotional": trade["usdt_notional"],
                                 "signal": trade["signal"], "pnl": trade["pnl"]})

    # ---- state ----
    async def _reconcile(self) -> None:
        """Adopt the real position from REST so restarts don't drift.

        No has_private_access check here: without credentials the call fails
        and we degrade to flat with a warning.
        """
        try:
            positions = await self.rest.positions()
        except OkxError as exc:
            log.warning("position reconcile failed: %s", exc)
            return
        pos = next((p for p in positions
                    if p.get("instId") == self.s.strategy_inst), None)
        qty = abs(float(pos.get("pos", "0") or 0)) if pos else 0.0
        if not pos or qty == 0:
            self.side, self.sz, self.entry_px = None, 0, None
            return
        if self.pos_mode == "net_mode":
            self.side = "long" if float(pos["pos"]) > 0 else "short"
        else:
            self.side = pos.get("posSide", "long")
        self.sz = int(qty)
        self.entry_px = float(pos.get("avgPx", "0") or 0) or None
        log.info("reconciled position: %s %d ct @ %s",
                 self.side, self.sz, self.entry_px)

    def status(self) -> dict:
        last = None
        if self.last_signal:
            last = {"action": self.last_signal.get("action"),
                    "label": self.last_signal.get("label")}
        return {"enabled": True, "inst": self.s.strategy_inst,
                "bar": self.s.strategy_bar,
                "sizeUsdt": self.s.strategy_size_usdt,
                "lever": self.s.strategy_lever,
                "position": {"side": self.side, "sz": self.sz,
                             "entryPx": self.entry_px},
                "realizedPnl": round(self.realized_pnl, 2),
                "lastSignal": last}
