# 🔭 夜观天象 · Astrolabe

**OKX market & account monitoring dashboard** — watch the stars, watch the market.

夜观天象，是一台「观测市场的星盘」：实时展示 OKX 公开行情与你自己的账户持仓盈亏，
并在行情异动时通过 Telegram 推送。**只观测，不下单** —— 无任何交易权限，安全开箱即用。

An OKX quant monitoring dashboard: real-time tickers, candlesticks, funding rates,
your positions & equity, plus Telegram alerts on market anomalies. **Observe, don't trade.**

![mode](https://img.shields.io/badge/mode-observe--only-blue) ![license](https://img.shields.io/badge/license-MIT-green)

## ✨ Features 特性

- 📈 **Watchlist 星象簿** — 主流币现货 + USDT 永续实时行情、24h 涨跌、资金费率
- 🕯️ **K 线观星图** — 1m K 线实时刷新（ECharts 蜡烛图）
- 🎯 **做T参考信号** — MA 金叉死叉 + RSI + 布林带合成「接回 / T出 / 观望」参考，直接标注在 K 线图上（仅观察参考，非投资建议）
- 💰 **司天监账户面板** — USDT 权益、持仓方向/数量/未实现盈亏（需只读 API Key）
- 🚨 **天象异动告警** — 价格短时剧烈波动 / 资金费率越阈 / 开平仓 / 权益变动，阈值可配置
- 📨 **Telegram 推送** — 告警实时送达手机
- 🌗 **模拟盘 / 实盘切换** — 默认模拟盘（`OKX_SIMULATED=1`）
- 🔓 **降级模式** — 不配置 API Key 也能跑：纯公开行情，账户面板显示引导
- 🔌 **断线自动重连** — OKX WebSocket 指数退避重连 + ping/pong 保活

## 🚀 Quick Start 快速开始

```bash
git clone https://github.com/<you>/Astrolabe.git
cd Astrolabe
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux

cp .env.example .env    # 按需填写 API Key / Telegram 配置
.venv/Scripts/python -m astrolabe.app
```

打开 **http://127.0.0.1:8765** — 观星开始。

## 🧭 交易功能（默认关闭）

Astrolabe 支持市价买/卖下单，但**默认关闭**，需要显式开启：

```env
OKX_TRADING_ENABLED=1   # 交易总开关，0/留空 = 只读观测
OKX_SIMULATED=1         # 强烈建议先在模拟盘跑通
```

安全护栏：

- `.env` 未开启时，下单接口一律 403，前端只显示引导文案
- API Key 未配置时同样拒绝下单
- 前端下单前强制弹出确认框（品种 / 数量 / 方向）
- 每次下单都会记录到告警流，配置 Telegram 后实时推送
- 现货下单数量单位为币（如 0.001 BTC），合约单位为张

⚠️ **风险提示**：开启交易即代表你接受真实下单的风险。请先用模拟盘（`OKX_SIMULATED=1`）验证全流程，实盘请使用小数量。

## 🔑 API Key 安全提示

- 在 OKX「API 管理」创建 Key 时**只勾选【读取】**，绝不勾选交易/提币。
- 建议先使用**模拟盘 Key**（`OKX_SIMULATED=1`）。
- `.env` 已在 `.gitignore` 中，**不会**被提交到仓库 —— Key 只存在你本地。

## ⭐ 开机自启（Windows）

```bat
copy scripts\start_astrolabe.vbs "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\"
```

登录 Windows 后自动后台启动星盘并打开看板；已在运行则只打开页面。
停止服务：运行 `scripts\stop_astrolabe.bat`。

## 🧪 Tests

```bash
.venv/Scripts/python -m pytest tests/ -v
```

## 🏗️ Architecture 架构

```
OKX REST/WS ──> okx_client ──> datafeed ──> broadcaster ──> Browser (ECharts)
                                   │
                                   ├──> alert_engine ──> telegram_notifier
                                   └──> storage (SQLite)
```

| 模块 | 职责 |
|---|---|
| `astrolabe/config.py` | 环境变量 / .env 加载 |
| `astrolabe/okx_client.py` | OKX REST 签名封装 + WS 端点 |
| `astrolabe/datafeed.py` | WS 订阅（公开 + 私有）、自动重连 |
| `astrolabe/broadcaster.py` | 事件扇出给浏览器客户端 |
| `astrolabe/alert_engine.py` | 异动规则引擎（含冷却机制） |
| `astrolabe/telegram_notifier.py` | Telegram Bot API 推送 |
| `astrolabe/storage.py` | SQLite 价格历史 / 快照 / 告警 |
| `astrolabe/app.py` | FastAPI 入口 + REST/WS API |
| `web/` | 单页看板（原生 JS + ECharts） |

## ⚠️ Disclaimer 免责声明

本项目仅用于行情观测与学习研究，不构成任何投资建议。加密货币风险极高，请自行控制风险。

This project is for market observation and educational purposes only. Not financial advice.

## License

MIT
