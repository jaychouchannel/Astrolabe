"""Tests for the strategy runner state machine (no network)."""

import asyncio
import time

from astrolabe.config import Settings
from astrolabe.okx_client import OkxError
from astrolabe.storage import Storage
from astrolabe.strategy import StrategyRunner, plan_actions


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


DOWNTREND = make_candles([200 - i for i in range(60)])   # -> buy_in
UPTREND = make_candles([100 + i for i in range(60)])     # -> sell_out
FLAT = make_candles([100.0] * 60)                        # -> hold


class FakeRest:
    """Records orders; serves canned candles. No network."""

    def __init__(self, candles: list[list], positions: list[dict] | None = None):
        self.candles_data = candles
        self.positions_data = positions or []
        self.orders: list[dict] = []
        self.fail_next_order = False

    async def instruments(self, inst_id: str) -> dict:
        return {"ctVal": "0.01", "minSz": "1"}

    async def account_config(self) -> dict:
        return {"posMode": "net_mode"}

    async def set_leverage(self, inst_id: str, lever: str, mgn_mode: str = "isolated") -> dict:
        return {"sCode": "0"}

    async def candles(self, inst_id: str, bar: str, limit: int = 100) -> list[list]:
        return self.candles_data

    async def ticker(self, inst_id: str) -> dict:
        return {"last": "120000"}

    async def positions(self) -> list[dict]:
        return self.positions_data

    async def place_order(self, inst_id: str, side: str, pos_side: str | None,
                          sz: int, td_mode: str = "isolated") -> dict:
        if self.fail_next_order:
            self.fail_next_order = False
            raise OkxError("order rejected 51000: insufficient funds")
        self.orders.append({"instId": inst_id, "side": side,
                            "posSide": pos_side, "sz": sz})
        return {"sCode": "0", "ordId": "123", "avgPx": "120000"}


def make_runner(candles, tmp_path, **kw) -> tuple[StrategyRunner, FakeRest]:
    s = Settings(trading_enabled=True, strategy_enabled=True,
                 strategy_size_usdt=100, strategy_lever=3,
                 strategy_cooldown_s=300, **kw)
    rest = FakeRest(candles)
    runner = StrategyRunner(s, rest, Storage(str(tmp_path / "t.db")))
    return runner, rest


def test_plan_actions_transitions():
    assert plan_actions(None, "long") == [{"action": "open", "direction": "long"}]
    assert plan_actions("long", "short") == [
        {"action": "close", "direction": "long"},
        {"action": "open", "direction": "short"}]
    assert plan_actions("long", None) == [{"action": "close", "direction": "long"}]
    assert plan_actions("long", "long") == []
    assert plan_actions(None, None) == []


def test_opens_long_on_buy_in(tmp_path):
    runner, rest = make_runner(DOWNTREND, tmp_path)
    asyncio.run(runner.step())
    assert len(rest.orders) == 1
    order = rest.orders[0]
    assert order["side"] == "buy" and order["posSide"] is None  # net mode
    assert order["sz"] == 1  # 100*3=300U notional budget < 1 contract -> minSz
    assert runner.side == "long" and runner.sz == 1
    assert runner.entry_px == 120000.0
    trades = runner.storage.recent_trades()
    assert len(trades) == 1 and trades[0]["action"] == "open"
    assert trades[0]["usdtNotional"] == 1200.0  # recorded honestly


def test_reverses_on_sell_out(tmp_path):
    runner, rest = make_runner(DOWNTREND, tmp_path)
    asyncio.run(runner.step())
    rest.candles_data = UPTREND
    runner.last_trade_ts = 0.0  # expire cooldown
    asyncio.run(runner.step())
    assert [o["side"] for o in rest.orders] == ["buy", "sell", "sell"]
    assert runner.side == "short"
    trades = runner.storage.recent_trades()
    assert [t["action"] for t in trades] == ["open", "close", "open"]  # newest first
    assert trades[1]["pnl"] == 0.0  # close @120000 == entry
    assert runner.realized_pnl == 0.0


def test_hold_keeps_position(tmp_path):
    runner, rest = make_runner(FLAT, tmp_path)
    asyncio.run(runner.step())
    assert rest.orders == [] and runner.side is None


def test_hold_while_holding_keeps_position(tmp_path):
    runner, rest = make_runner(DOWNTREND, tmp_path)
    asyncio.run(runner.step())  # open long
    rest.candles_data = FLAT
    runner.last_trade_ts = 0.0  # expire cooldown
    asyncio.run(runner.step())  # hold while holding -> no close
    assert len(rest.orders) == 1 and runner.side == "long"


def test_cooldown_suppresses_retrigger(tmp_path):
    runner, rest = make_runner(DOWNTREND, tmp_path)
    asyncio.run(runner.step())
    rest.candles_data = UPTREND
    asyncio.run(runner.step())  # cooldown still active (last_trade_ts just set)
    assert len(rest.orders) == 1 and runner.side == "long"


def test_order_failure_records_error(tmp_path):
    runner, rest = make_runner(DOWNTREND, tmp_path)
    rest.fail_next_order = True
    asyncio.run(runner.step())
    assert rest.orders == [] and runner.side is None
    trades = runner.storage.recent_trades()
    assert len(trades) == 1 and trades[0]["action"] == "error"


def test_reconcile_from_positions(tmp_path):
    rest = FakeRest(FLAT, positions=[
        {"instId": "BTC-USDT-SWAP", "pos": "2", "avgPx": "118000"}])
    s = Settings(trading_enabled=True, strategy_enabled=True)
    runner = StrategyRunner(s, rest, Storage(str(tmp_path / "t.db")))
    asyncio.run(runner._reconcile())
    assert runner.side == "long" and runner.sz == 2 and runner.entry_px == 118000.0
