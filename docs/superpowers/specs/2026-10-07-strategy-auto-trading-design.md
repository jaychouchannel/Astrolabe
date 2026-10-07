# 自动交易策略(观星执行)设计

日期:2026-10-07
状态:已确认(用户选定:沿用做T信号自动化 / 内嵌后台任务 / BTC-USDT-SWAP / 1x 双向 / 固定 100U 单仓)
定位:模拟盘小游戏。用户自行为 OKX 模拟盘充值,价格与实盘 API 一致,纯自娱自乐,不构成投资功能。

## 目标

把现有「做T参考信号」(MA5/20 金叉死叉 + RSI + 布林带 → 接回/T出/观望)
接上执行层:信号触发后自动在 BTC-USDT-SWAP 上下市价单,先只跑模拟盘。
面板可见策略状态与成交记录,每笔成交流水入库并推送 Telegram。

## 拆分

两个叠层 PR:

1. **PR1 后端**:okx_client 下单能力 + strategy 引擎 + 配置/存储/API + 单测 + 本设计文档
2. **PR2 前端**:面板策略卡片(状态 + 成交流水),基于 PR1 分支

## 三道安全闸门(缺一不下单)

| 开关 | 环境变量 | 默认 | 含义 |
|------|----------|------|------|
| 交易执行 | `OKX_TRADING_ENABLED` | 0 | okx_client 层:关着时 `place_order` 直接抛错 |
| 策略运行 | `STRATEGY_ENABLED` | 0 | 策略层:关着时后台循环不启动 |
| 实盘许可 | `STRATEGY_ALLOW_LIVE` | 0 | 仅当 `OKX_SIMULATED=0`(实盘头)时才检查;默认禁止策略碰实盘 |

## 后端

### 1. `okx_client.py` 新增下单能力

- `instruments(inst_id)`:`GET /api/v5/public/instruments?instType=SWAP&instId=`,
  取 `ctVal`(BTC-USDT-SWAP 每张 0.01 BTC)与 `minSz`,进程内缓存。
- `account_config()`:`GET /api/v5/account/config`,读 `posMode`
  (`net_mode` / `long_short_mode`),启动时读一次。
- `place_order(...)`:POST /api/v5/trade/order,市价单(`ordType="market"`,
  `tdMode="isolated"`)。按 posMode 适配两种格式:
  - net 模式:开多=`side=buy`,开空=`side=sell`,平仓为反向单,不带 posSide;
  - long/short 模式:开多=`side=buy, posSide=long`,平多=`side=sell, posSide=long`,
    开空/平空镜像。
- **第一道闸门**在这里:`trading_enabled=False` 时 `place_order` 抛 `OkxError`。
- 张数换算:`sz = floor(size_usdt / (price * ctVal))`,结果 < minSz 时
  记日志并放弃(不报错刷屏)。

### 2. `strategy.py` 新增策略引擎

`StrategyRunner(settings, rest, storage, on_event)`,asyncio 任务,
随 FastAPI lifespan 启停。纯状态机,单测用假 rest 驱动:

- 周期:每 `STRATEGY_POLL_S`(默认 60s)拉 `STRATEGY_BAR`(默认 1m)K线
  (limit=100)→ `compute_signals()`(复用现有引擎,不改动)。
- 目标仓位由信号映射:`buy_in`→多,`sell_out`→空,`hold`→不变。
- 动作:
  - 目标与当前一致 → 什么都不做;
  - 空仓 → 开目标方向;
  - 持仓且与目标相反 → 先市价平仓,再开新方向(视为一次反手,两笔成交);
  - 持仓且信号为 hold → 不动(持有到反手信号出现)。
- 冷却:任意一笔成交后 `STRATEGY_COOLDOWN_S`(默认 300s)内不再触发,
  防同一根K线内反复打单。
- 对账:启动时用 `rest.positions()` 读真实持仓初始化状态;下单请求发出但
  结果未知(超时/异常)时,下个周期先强制对账再决策。
- 每次成交(开/平/反手)与每次下单失败:
  - 写 SQLite `trades` 表;
  - 经回调推 WebSocket(topic=`strategy`)给面板;
  - 推 Telegram(纯文本,复用 notifier,新增 `send_text` 方法)。

### 3. `config.py` 新增配置

`strategy_enabled`、`trading_enabled`、`allow_live`、`strategy_inst`
(默认 `BTC-USDT-SWAP`)、`strategy_bar`(默认 `1m`)、`strategy_size_usdt`
(默认 100)、`strategy_poll_s`(默认 60)、`strategy_cooldown_s`(默认 300),
全部可用环境变量覆盖,风格与现有一致。

### 4. `storage.py` 新增 trades 表

`(ts, inst_id, action[open/close], direction[long/short], sz, px,
usdt_notional, signal, pnl)`。平仓时由成交价与开仓价算出已实现盈亏
记录进 `pnl`(开仓记录为 0)。提供 `record_trade()` / `recent_trades(limit=50)`。

### 5. `app.py` 接线

- lifespan 内按三道闸门决定是否启动 StrategyRunner。
- `GET /api/strategy`:策略配置、当前持仓(side/sz/entryPx)、最近一次
  信号摘要、最近 20 笔成交。
- 成交经 broadcaster 广播,面板实时刷新。

## 前端(PR2)

面板新增「观星执行」卡片:

- 状态行:运行中/已停用、品种、方向持仓(多/空/空仓 + 张数 + 开仓价)、
  累计已实现盈亏。
- 成交流水表(最近 20 笔:时间/方向/开平/张数/价格/盈亏)。
- 角落注明「模拟盘小游戏,非投资建议」。

## 测试

`tests/test_strategy.py`,假 rest 假 candles 驱动状态机:

- 空仓 + buy_in → 开多一笔;
- 持多 + sell_out → 平多 + 开空两笔;
- hold → 不动;
- 冷却期内同信号 → 不重复下单;
- 三道闸门任一关闭 → 零下单;
- 张数换算(price × ctVal)与 minSz 放弃;
- 下单抛错 → 状态不变、错误入库/推送;
- `tests/test_okx_client.py` 补 place_order 的头与闸门。

## 明确不做

- 不做止损/止盈(用户选定固定仓位纯信号驱动)。
- 不做网格、不做回测、不做多品种。
- 不改 signals.py 的指标逻辑。
- 默认配置下行为与现状完全一致(三个开关全关,零下单路径)。
