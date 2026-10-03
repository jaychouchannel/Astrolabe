# 夜观天象 (Astrolabe) — 设计文档

日期：2026-10-03

## 一句话

OKX 量化行情监控 + 交易看板：Web 实时展示公开行情与账户持仓盈亏，Telegram 推送异动告警。只读观测，不下单。

## 需求

- 数据范围：OKX 公开行情 + 私有账户数据（余额、持仓、盈亏），支持模拟盘 / 实盘切换。
- 品种：主流币（BTC、ETH、SOL 等）USDT 本位现货 + 永续合约，watchlist 可配置。
- 交付形态：本地 Web 仪表盘（WebSocket 实时刷新）+ Telegram 异动推送。
- 降级：未配置 API Key 时自动降级为纯公开行情模式。
- 用途：个人自用，代码开源到 GitHub。

## 架构

```
OKX REST/WS ──> okx_client ──> datafeed ──> broadcaster ──> 浏览器 (ECharts)
                                   │
                                   ├──> alert_engine ──> telegram_notifier
                                   └──> storage (SQLite)
```

### 模块

| 模块 | 职责 |
|---|---|
| `config` | 环境变量加载：API Key、模拟盘开关、watchlist、告警阈值、Telegram 配置 |
| `okx_client` | OKX REST（签名）+ WebSocket（公开/私有频道）统一封装 |
| `datafeed` | 订阅 K 线、Ticker、资金费率、账户、持仓频道；断线指数退避重连 |
| `broadcaster` | 数据扇出给所有浏览器 WS 客户端 |
| `alert_engine` | 规则引擎：价格短时大幅波动、资金费率越阈、持仓/余额变化 |
| `telegram_notifier` | 消费告警事件，HTTP 调用 Telegram Bot API |
| `storage` | SQLite 存价格历史、账户快照、告警历史 |
| `app` | FastAPI 入口：静态页面、WS 端点、REST 快照端点 |

### 技术选型

Python 3.11+ / FastAPI / uvicorn / httpx / websockets / SQLite（标准库 sqlite3）/
前端原生 HTML+JS + ECharts（CDN）。Telegram 用 httpx 直连 Bot API，不引入额外 SDK。
OKX 签名（HMAC-SHA256 + Base64）自行实现，避免第三方 SDK 版本耦合。

### 关键细节

- WS 私有频道需先发 `login`；公开频道直接 `subscribe`。
- ping/pong 保活（OKX 要求 25s 内响应 `ping`）。
- 告警规则默认值：单币 5 分钟涨跌超 2%、资金费率绝对值超 0.1%、持仓数量变化。
- 所有规则阈值可在 `.env` / 看板配置中调整。

## 错误处理

- WS 断线：指数退避重连（1s 起，上限 60s）。
- REST 限频：遵循 OKX 429 响应，退避后重试。
- API Key 缺失/无效：跳过私有订阅，账户面板显示引导文案。

## 测试

- 告警引擎、OKX 签名、配置加载：pytest 单元测试（mock 网络层）。
- 前端与真实数据流：连接 OKX 模拟盘手动验证。

## 仓库约定

- 目录：`D:\Astrolabe`，分支 `main`。
- README 中英双语；MIT License；提供 `.env.example`。
