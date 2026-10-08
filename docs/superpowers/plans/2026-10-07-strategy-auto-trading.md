# 观星执行(策略自动交易,PR1 后端)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有做T信号接上执行层:后台循环拉 BTC-USDT-SWAP 1m K线 → 复用 `compute_signals()` → 自动市价开多/开空/反手,先跑 OKX 模拟盘,成交入库 + 推 Telegram + 面板 API。

**Architecture:** 在现有 FastAPI 应用内新增 `StrategyRunner` asyncio 后台任务;`okx_client.py` 增加下单能力;三道环境变量闸门(`OKX_TRADING_ENABLED` / `STRATEGY_ENABLED` / `STRATEGY_ALLOW_LIVE`)全部默认关闭,默认行为与现状完全一致。纯决策逻辑(`plan_actions` / `calc_contracts`)与 IO 分离,便于单测。

**Tech Stack:** Python 3.11+ / FastAPI / httpx / SQLite / pytest(asyncio.run 同步包装,沿用现有测试风格)

## Global Constraints

- 规格:`docs/superpowers/specs/2026-10-07-strategy-auto-trading-design.md`
- 不修改 `astrolabe/signals.py` 的指标逻辑,只复用 `compute_signals(candles) -> dict`(candles 为 OKX newest-first 格式)。
- 三道闸门默认全关:`OKX_TRADING_ENABLED=0`、`STRATEGY_ENABLED=0`、`STRATEGY_ALLOW_LIVE=0`;默认配置下零下单路径。
- 规格偏差(已确认的现实约束):BTC-USDT-SWAP 每张 ctVal=0.01 BTC ≈ 1000+ USDT,100U@1x 买不起一张。调整为:名义预算 = `STRATEGY_SIZE_USDT × STRATEGY_LEVER`(杠杆默认 3,隔离模式,启动时调 set-leverage),张数 = floor(名义预算 / (price×ctVal)),不足 `minSz` 时取 `minSz`(即至少 1 张)并在成交记录里如实记名义值。
- Telegram 推送复用现有 `TelegramNotifier.send(text)`(spec 里说"新增 send_text"——`send` 已是通用文本方法,不新增)。
- 代码风格与现有一致:模块英文 docstring,注释克制,类型标注,`from __future__ import annotations`。
- 所有 pytest 不得访问网络;`OkxRestClient` 不可实例化后调网络方法(仅测头/纯函数/闸门)。

---

### Task 1: 配置项 + .env.example

**Files:**
- Modify: `astrolabe/config.py`(Settings 字段 + load_settings)
- Modify: `.env.example`
- Test: `tests/test_strategy_config.py`(新建)

**Interfaces:**
- Produces: `Settings` 新字段 `trading_enabled: bool`、`strategy_enabled: bool`、`allow_live: bool`、`strategy_inst: str`、`strategy_bar: str`、`strategy_size_usdt: float`、`strategy_lever: int`、`strategy_poll_s: int`、`strategy_cooldown_s: int`(后续所有任务读取这些)。

- [ ] **Step 1: Write the failing test**

新建 `tests/test_strategy_config.py`:

```python
"""Tests for strategy auto-trading settings gates."""

from astrolabe.config import Settings


def test_strategy_defaults_all_off():
    s = Settings()
    assert s.trading_enabled is False
    assert s.strategy_enabled is False
    assert s.allow_live is False
    assert s.strategy_inst == "BTC-USDT-SWAP"
    assert s.strategy_bar == "1m"
    assert s.strategy_size_usdt == 100.0
    assert s.strategy_lever == 3
    assert s.strategy_poll_s == 60
    assert s.strategy_cooldown_s == 300


def test_strategy_gates_can_be_enabled():
    s = Settings(trading_enabled=True, strategy_enabled=True, allow_live=True)
    assert s.trading_enabled and s.strategy_enabled and s.allow_live
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/Astrolabe && python -m pytest tests/test_strategy_config.py -v`
Expected: FAIL with `TypeError: Settings.__init__() got an unexpected keyword argument 'trading_enabled'`

- [ ] **Step 3: Write minimal implementation**

`astrolabe/config.py` — 在 `Settings` 的 `# server` 段之前加入:

```python
    # strategy auto-trading (观星执行) — all gates default OFF
    trading_enabled: bool = False     # okx_client.place_order 总闸
    strategy_enabled: bool = False    # 策略后台循环开关
    allow_live: bool = False          # 实盘许可 (仅 simulated=False 时需要)
    strategy_inst: str = "BTC-USDT-SWAP"
    strategy_bar: str = "1m"
    strategy_size_usdt: float = 100.0  # 每笔保证金预算 (USDT)
    strategy_lever: int = 3            # 隔离杠杆倍数
    strategy_poll_s: int = 60
    strategy_cooldown_s: int = 300
```

在 `load_settings` 的 `db_path=...` 行之前加入:

```python
        trading_enabled=os.environ.get("OKX_TRADING_ENABLED", "0") in ("1", "true", "True"),
        strategy_enabled=os.environ.get("STRATEGY_ENABLED", "0") in ("1", "true", "True"),
        allow_live=os.environ.get("STRATEGY_ALLOW_LIVE", "0") in ("1", "true", "True"),
        strategy_inst=os.environ.get("STRATEGY_INST", "BTC-USDT-SWAP"),
        strategy_bar=os.environ.get("STRATEGY_BAR", "1m"),
        strategy_size_usdt=float(os.environ.get("STRATEGY_SIZE_USDT", "100")),
        strategy_lever=int(os.environ.get("STRATEGY_LEVER", "3")),
        strategy_poll_s=int(os.environ.get("STRATEGY_POLL_S", "60")),
        strategy_cooldown_s=int(os.environ.get("STRATEGY_COOLDOWN_S", "300")),
```

`.env.example` 在 `# ===== 服务 =====` 之前加入:

```
# ===== 自动交易策略 (观星执行, 默认全关) =====
# 三道闸门全部打开才会真的下单; OKX_SIMULATED=1 时走模拟盘
OKX_TRADING_ENABLED=0     # okx_client 下单能力总闸
STRATEGY_ENABLED=0        # 策略后台循环开关
STRATEGY_ALLOW_LIVE=0     # 实盘许可 (仅 OKX_SIMULATED=0 时检查, 默认禁止策略碰实盘)
STRATEGY_INST=BTC-USDT-SWAP
STRATEGY_BAR=1m
STRATEGY_SIZE_USDT=100    # 每笔保证金预算 (USDT); 名义预算 = 保证金 x 杠杆
STRATEGY_LEVER=3          # 隔离杠杆; BTC 一张合约名义约 price*0.01 USDT, 请确保名义预算 >= 一张
STRATEGY_POLL_S=60        # 信号轮询间隔 (秒)
STRATEGY_COOLDOWN_S=300   # 成交后的冷却时间 (秒)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_strategy_config.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add astrolabe/config.py .env.example tests/test_strategy_config.py
git commit -m "feat: strategy auto-trading config gates (all default off)"
```

---

### Task 2: okx_client 下单能力

**Files:**
- Modify: `astrolabe/okx_client.py`
- Test: `tests/test_okx_client.py`(追加)

**Interfaces:**
- Consumes: `Settings.trading_enabled`(Task 1)。
- Produces:
  - `calc_contracts(margin_usdt: float, lever: int, price: float, ct_val: float, min_sz: float) -> int`(模块级纯函数)
  - `OkxRestClient.instruments(inst_id: str) -> dict`(ctVal/minSz,进程内缓存)
  - `OkxRestClient.account_config() -> dict`(含 posMode)
  - `OkxRestClient.set_leverage(inst_id: str, lever: str, mgn_mode: str = "isolated") -> dict`
  - `OkxRestClient.place_order(inst_id: str, side: str, pos_side: str | None, sz: int, td_mode: str = "isolated") -> dict`(市价单;闸门关闭时抛 `OkxError`)
  - `_request` 新增可选参数 `body: str = ""`(POST JSON 用,现有调用不受影响)

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_okx_client.py`(顶部 import 行补 `import pytest` 和 `from astrolabe.okx_client import ... calc_contracts`):

```python
import pytest

from astrolabe.okx_client import OkxRestClient, calc_contracts, sign, OkxError


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
```

注意:`tests/test_okx_client.py` 现无 pytest-asyncio 依赖;若项目未装 pytest-asyncio,把 async 测试改用 `asyncio.run(...)` 包装:

```python
def test_place_order_gated_by_trading_enabled():
    import asyncio
    client = OkxRestClient(Settings())
    with pytest.raises(OkxError, match="OKX_TRADING_ENABLED"):
        asyncio.run(client.place_order("BTC-USDT-SWAP", "buy", None, 1))
```

先看 `requirements.txt` 有无 pytest-asyncio,有则用 `@pytest.mark.asyncio` 风格,没有则统一用 `asyncio.run` 包装(与现有测试库保持零新增依赖)。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_okx_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'calc_contracts'`

- [ ] **Step 3: Write minimal implementation**

`astrolabe/okx_client.py` 改动:

模块级纯函数(放在 `OkxError` 定义之后):

```python
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
```

`OkxRestClient.__init__` 末尾加缓存:

```python
        self._instruments: dict[str, dict] = {}
```

`_request` 增加 body 参数(方法签名与第一行改为):

```python
    async def _request(self, method: str, path: str, params: dict | None = None,
                       body: str = "") -> Any:
        request_path = path
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            request_path = f"{path}?{query}"
```

(其余不变——`content=body if method == "POST" else None` 和签名头已兼容。)

私有方法 + 公有方法(加在 `positions()` 之后):

```python
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
                           mgn_mode: str = "isolated") -> dict:
        data = await self._request(
            "POST", "/api/v5/account/set-leverage",
            body=json.dumps({"instId": inst_id, "lever": lever, "mgnMode": mgn_mode}),
        )
        return data[0]

    async def place_order(self, inst_id: str, side: str, pos_side: str | None,
                          sz: int, td_mode: str = "isolated") -> dict:
        """Market order. Gate: raises unless OKX_TRADING_ENABLED=1."""
        if not self.s.trading_enabled:
            raise OkxError("trading disabled: set OKX_TRADING_ENABLED=1")
        payload = json.dumps(
            self._order_body(inst_id, side, pos_side, sz, td_mode))
        data = await self._request("POST", "/api/v5/trade/order", body=payload)
        order = data[0]
        if order.get("sCode") != "0":
            raise OkxError(f"order rejected {order.get('sCode')}: {order.get('sMsg')}")
        return order
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_okx_client.py tests/test_strategy_config.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add astrolabe/okx_client.py tests/test_okx_client.py
git commit -m "feat: okx order placement behind OKX_TRADING_ENABLED gate"
```

---

### Task 3: storage trades 表

**Files:**
- Modify: `astrolabe/storage.py`
- Test: `tests/test_storage.py`(追加)

**Interfaces:**
- Produces:
  - `Storage.record_trade(trade: dict) -> None`,trade 键:`ts, inst_id, action, direction, sz, px, usdt_notional, signal, pnl`(action ∈ open/close/error;direction ∈ long/short)
  - `Storage.recent_trades(limit: int = 50) -> list[dict]`(新→旧,键同上 + `ts`)

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_storage.py`(沿用该文件现有 fixture 风格,若其用 `tmp_path` 建库则保持一致;以下假设有 `storage` fixture,若无则函数内自建 `Storage(str(tmp_path / "t.db"))`):

```python
def test_trade_roundtrip(tmp_path):
    from astrolabe.storage import Storage
    st = Storage(str(tmp_path / "t.db"))
    st.record_trade({"ts": 100.0, "inst_id": "BTC-USDT-SWAP", "action": "open",
                     "direction": "long", "sz": 1, "px": 120000.0,
                     "usdt_notional": 1200.0, "signal": "buy_in", "pnl": 0.0})
    st.record_trade({"ts": 200.0, "inst_id": "BTC-USDT-SWAP", "action": "close",
                     "direction": "long", "sz": 1, "px": 121000.0,
                     "usdt_notional": 1210.0, "signal": "sell_out", "pnl": 10.0})
    rows = st.recent_trades()
    assert [r["action"] for r in rows] == ["close", "open"]  # newest first
    assert rows[0]["pnl"] == 10.0 and rows[1]["direction"] == "long"
    assert st.recent_trades(limit=1)[0]["action"] == "close"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_storage.py -v`
Expected: FAIL with `AttributeError: 'Storage' object has no attribute 'record_trade'`

- [ ] **Step 3: Write minimal implementation**

`astrolabe/storage.py` — `_init_schema` 的 executescript 里追加:

```sql
            CREATE TABLE IF NOT EXISTS trades (
                ts REAL NOT NULL, inst_id TEXT NOT NULL, action TEXT NOT NULL,
                direction TEXT NOT NULL, sz INTEGER NOT NULL, px REAL NOT NULL,
                usdt_notional REAL NOT NULL, signal TEXT NOT NULL, pnl REAL NOT NULL
            );
```

writes 段追加:

```python
    def record_trade(self, trade: dict) -> None:
        self._db.execute(
            "INSERT INTO trades(ts, inst_id, action, direction, sz, px, "
            "usdt_notional, signal, pnl) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trade["ts"], trade["inst_id"], trade["action"], trade["direction"],
             trade["sz"], trade["px"], trade["usdt_notional"],
             trade["signal"], trade["pnl"]),
        )
        self._db.commit()
```

reads 段追加:

```python
    def recent_trades(self, limit: int = 50) -> list[dict]:
        rows = self._db.execute(
            "SELECT ts, inst_id, action, direction, sz, px, usdt_notional, "
            "signal, pnl FROM trades ORDER BY ts DESC LIMIT ?", (limit,),
        ).fetchall()
        return [
            {"ts": r[0], "instId": r[1], "action": r[2], "direction": r[3],
             "sz": r[4], "px": r[5], "usdtNotional": r[6], "signal": r[7],
             "pnl": r[8]}
            for r in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_storage.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add astrolabe/storage.py tests/test_storage.py
git commit -m "feat: trades table + record/recent API"
```

---

### Task 4: strategy.py 状态机引擎

**Files:**
- Create: `astrolabe/strategy.py`
- Test: `tests/test_strategy.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 Settings 字段;Task 2 的 `calc_contracts` / `instruments` / `account_config` / `set_leverage` / `place_order`;Task 3 的 `record_trade` / `recent_trades`;现有 `compute_signals`。
- Produces:
  - `plan_actions(current: str | None, target: str | None) -> list[dict]`(模块级纯函数;元素 `{"action": "open"|"close", "direction": "long"|"short"}`)
  - `TARGET_FROM_ACTION = {"buy_in": "long", "sell_out": "short", "hold": None}`
  - `StrategyRunner(settings, rest, storage, on_trade=None)`:
    - `async start()`(拉合约参数/账户 posMode/set-leverage/对账,然后起后台循环)
    - `async stop()`
    - `async step()`(拉K线→算信号→`async on_signal(signal)`,测试直接调它)
    - 属性:`side: str | None`、`sz: int`、`entry_px: float | None`、`realized_pnl: float`、`last_signal: dict | None`
    - `status() -> dict`(面板/API 用:`{enabled, inst, bar, sizeUsdt, lever, position: {side, sz, entryPx}, realizedPnl, lastSignal: {action, label}}`)

- [ ] **Step 1: Write the failing test**

新建 `tests/test_strategy.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'astrolabe.strategy'`

- [ ] **Step 3: Write minimal implementation**

新建 `astrolabe/strategy.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_strategy.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add astrolabe/strategy.py tests/test_strategy.py
git commit -m "feat: strategy runner — signal-driven state machine with gates"
```

---

### Task 5: app.py 接线 + /api/strategy + README

**Files:**
- Modify: `astrolabe/app.py`
- Modify: `README.md`(仅"只观测,不下单"段落)
- Test: 手动验证(见 Step 4;纯接线无新逻辑,不为其加网络测试)

**Interfaces:**
- Consumes: Task 4 的 `StrategyRunner` / `status()`;Task 3 的 `recent_trades`;现有 `broadcaster` / `notifier`。
- Produces: `GET /api/strategy` → `{"enabled": bool, "tradingEnabled": bool, "inst", "bar", "sizeUsdt", "lever", "position", "realizedPnl", "lastSignal", "recentTrades"}` 或 `{"enabled": false, "tradingEnabled": bool, "reason": str}`。

- [ ] **Step 1: 实现接线**

`astrolabe/app.py` 改动:

import 区新增:

```python
from .strategy import StrategyRunner
```

`broadcaster = Broadcaster()` 之后:

```python
runner: StrategyRunner | None = None
```

`_push_alert` 之后新增回调与启动辅助:

```python
async def _on_strategy_trade(trade: dict) -> None:
    await broadcaster.broadcast({"topic": "strategy", "data": trade})
    if notifier.enabled:
        text = (f"🤖 观星执行 {trade['action'].upper()} {trade['direction']} "
                f"{trade['sz']}张 @ {trade['px']} (信号:{trade['signal']})")
        if trade["pnl"]:
            text += f" 已实现盈亏:{trade['pnl']:+.2f} USDT"
        await notifier.send(text)
```

lifespan 内 `await feed.start()` 之前:

```python
    global runner
    if settings.strategy_enabled:
        if not settings.simulated and not settings.allow_live:
            log.warning("策略未启动:实盘需 STRATEGY_ALLOW_LIVE=1")
        else:
            runner = StrategyRunner(settings, rest, storage, on_trade=_on_strategy_trade)
            try:
                await runner.start()
            except Exception:
                log.exception("strategy runner failed to start")
                runner = None
```

lifespan 的 `yield` 之后、`storage.close()` 之前:

```python
    if runner:
        await runner.stop()
```

`/api/alerts` 之前新增端点:

```python
@app.get("/api/strategy")
async def api_strategy() -> JSONResponse:
    """观星执行状态 — 模拟盘小游戏,非投资建议。"""
    if runner is None:
        return JSONResponse({
            "enabled": False,
            "tradingEnabled": settings.trading_enabled,
            "reason": "STRATEGY_ENABLED=0 或实盘未授权 (STRATEGY_ALLOW_LIVE)"})
    payload = runner.status()
    payload["tradingEnabled"] = settings.trading_enabled
    payload["recentTrades"] = storage.recent_trades(20)
    return JSONResponse(payload)
```

README 的 `**只观测，不下单**` 段落改为:

```markdown
**默认只观测，不下单** —— 所有交易能力默认关闭,安全开箱即用。可选的
「观星执行」策略模块在 `OKX_TRADING_ENABLED=1` + `STRATEGY_ENABLED=1`
(+ 实盘还需 `STRATEGY_ALLOW_LIVE=1`) 三道闸门全部打开后,才会把做T信号
转成 BTC-USDT-SWAP 市价单,推荐配合 `OKX_SIMULATED=1` 模拟盘使用。仅供
学习娱乐,非投资建议。
```

- [ ] **Step 2: 全量测试**

Run: `python -m pytest -v`
Expected: 全部 passed(现有测试 + 新增测试,零失败)

- [ ] **Step 3: Commit**

```bash
git add astrolabe/app.py README.md
git commit -m "feat: wire strategy runner into app + /api/strategy endpoint"
```

- [ ] **Step 4: 手动验证(模拟盘)**

1. 用户 `.env` 需要 OKX 模拟盘 API(交易权限)+ `OKX_SIMULATED=1`,并打开三道闸门。
2. Run: `python -m astrolabe.app`
3. 启动日志应出现 `strategy runner on BTC-USDT-SWAP (1m, ...)`;60 秒内应看到一条 STRATEGY 日志或"观望"下的静默。
4. 浏览器打开 `http://127.0.0.1:8765/api/strategy`,确认返回 `enabled: true`、持仓与 recentTrades。
5. OKX 模拟盘网页确认有成交;Telegram(若配置)收到推送。
6. 关掉 `OKX_TRADING_ENABLED=0` 再启动,确认日志无 STRATEGY 成交、`/api/strategy` 返回 `tradingEnabled: false`。

- [ ] **Step 5: push 分支**

```bash
git push -u origin feat/strategy-auto-trading
```

(注:SSH remote;push 前与用户确认是否现在推。)
