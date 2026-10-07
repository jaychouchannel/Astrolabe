"""FastAPI entry point: dashboard page, WS endpoint, REST API.

Run:  python -m astrolabe.app   (or: uvicorn astrolabe.app:app)
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .alert_engine import AlertEngine
from .broadcaster import Broadcaster
from .config import load_settings
from .datafeed import Datafeed
from .okx_client import OkxRestClient
from .signals import compute_signals
from .storage import Storage
from .strategy import StrategyRunner
from .telegram_notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("astrolabe")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

settings = load_settings()
rest = OkxRestClient(settings)
storage = Storage(settings.db_path)
engine = AlertEngine(settings)
notifier = TelegramNotifier(settings)
broadcaster = Broadcaster()

runner: StrategyRunner | None = None

state: dict = {
    "tickers": {},      # instId -> ticker dict
    "funding": {},      # swap instId -> funding rate %
    "balance": None,    # USDT balance dict or None
    "positions": [],
    "alerts": [],       # latest alerts (also persisted)
    "mode": "demo" if settings.simulated else "live",
    "private": settings.has_private_access,
}


async def _push_alert(alert) -> None:
    state["alerts"] = ([alert.as_dict()] + state["alerts"])[:100]
    storage.record_alerts([alert])
    log.info("ALERT %s", alert.message)
    await broadcaster.broadcast({"topic": "alert", "data": alert.as_dict()})
    if notifier.enabled:
        asyncio.create_task(notifier.send_alert(alert))


async def _on_strategy_trade(trade: dict) -> None:
    await broadcaster.broadcast({"topic": "strategy", "data": trade})
    if notifier.enabled:
        text = (f"🤖 观星执行 {trade['action'].upper()} {trade['direction']} "
                f"{trade['sz']}张 @ {trade['px']} (信号:{trade['signal']})")
        if trade["pnl"]:
            text += f" 已实现盈亏:{trade['pnl']:+.2f} USDT"
        await notifier.send(text)


async def handle_event(channel: str, item: dict) -> None:
    if channel == "tickers":
        inst_id = item.get("instId", "")
        last = item.get("last")
        state["tickers"][inst_id] = item
        try:
            price = float(last)
            storage.record_price(inst_id, price)
            for alert in engine.on_ticker(inst_id, price, time.time()):
                await _push_alert(alert)
        except (TypeError, ValueError):
            pass
        await broadcaster.broadcast({"topic": "ticker", "data": item})

    elif channel == "funding-rate":
        inst_id = item.get("instId", "")
        try:
            rate_pct = float(item.get("fundingRate", "0")) * 100
        except (TypeError, ValueError):
            return
        state["funding"][inst_id] = rate_pct
        for alert in engine.on_funding_rate(inst_id, rate_pct):
            await _push_alert(alert)
        await broadcaster.broadcast({"topic": "funding", "data": item})

    elif channel.startswith("candle"):
        await broadcaster.broadcast(
            {"topic": "candle", "bar": channel[len("candle"):], "data": item}
        )

    elif channel == "account":
        try:
            details = item.get("details", [])
            usdt = next((d for d in details if d.get("ccy") == "USDT"), None)
        except AttributeError:
            return
        if usdt:
            equity = float(usdt.get("eq", "0") or 0)
            state["balance"] = usdt
            storage.record_snapshot({"equity": equity, "ts": time.time()})
            for alert in engine.on_balance(equity):
                await _push_alert(alert)
            await broadcaster.broadcast({"topic": "balance", "data": usdt})

    elif channel == "positions":
        # OKX pushes single-position updates; refetch full list for a coherent view
        if settings.has_private_access:
            try:
                state["positions"] = await rest.positions()
            except Exception as exc:
                log.warning("positions refresh failed: %s", exc)
                return
        for alert in engine.on_positions(state["positions"]):
            await _push_alert(alert)
        await broadcaster.broadcast({"topic": "positions", "data": state["positions"]})


async def _seed_state() -> None:
    """Prime in-memory state via REST so the dashboard isn't empty on load."""
    watch = set(settings.watchlist)
    try:
        for t in await rest.tickers("SPOT"):
            if t["instId"] in watch:
                state["tickers"][t["instId"]] = t
        for t in await rest.tickers("SWAP"):
            if t["instId"] in watch:
                state["tickers"][t["instId"]] = t
    except Exception as exc:
        log.warning("seed tickers failed: %s", exc)
    for inst_id in settings.swap_instruments:
        try:
            fr = await rest.funding_rate(inst_id)
            state["funding"][inst_id] = float(fr.get("fundingRate", "0")) * 100
        except Exception:
            pass
    if settings.has_private_access:
        try:
            state["balance"] = await rest.balance("USDT")
            state["positions"] = await rest.positions()
        except Exception as exc:
            log.warning("seed account failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    await _seed_state()
    feed = Datafeed(settings, handle_event)
    await feed.start()
    log.info("Astrolabe 夜观天象 started — mode=%s private=%s watchlist=%s",
             state["mode"], state["private"], settings.watchlist)
    yield
    if runner:
        await runner.stop()
    await feed.stop()
    await rest.close()
    await notifier.close()
    storage.close()


app = FastAPI(title="Astrolabe 夜观天象", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/api/state")
async def api_state() -> JSONResponse:
    return JSONResponse({
        "mode": state["mode"],
        "private": state["private"],
        "watchlist": settings.watchlist,
        "timeframes": settings.chart_timeframes,
        "tickers": state["tickers"],
        "funding": state["funding"],
        "balance": state["balance"],
        "positions": state["positions"],
        "alerts": state["alerts"][:50],
    })


@app.get("/api/candles")
async def api_candles(instId: str, bar: str = "1m") -> JSONResponse:
    try:
        return JSONResponse(await rest.candles(instId, bar))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


SIGNALS_CACHE_TTL_S = 30
_signals_cache: dict[tuple[str, str], tuple[float, dict]] = {}


@app.get("/api/signals")
async def api_signals(instId: str, bar: str = "1m") -> JSONResponse:
    """做T参考信号 (MA cross + RSI + Bollinger) — 仅观察参考，非投资建议。"""
    key = (instId, bar)
    now = time.time()
    cached = _signals_cache.get(key)
    if cached and now - cached[0] < SIGNALS_CACHE_TTL_S:
        return JSONResponse(cached[1])
    try:
        candles = await rest.candles(instId, bar, limit=100)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    result = compute_signals(candles)
    _signals_cache[key] = (now, result)
    return JSONResponse(result)


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


@app.get("/api/alerts")
async def api_alerts() -> JSONResponse:
    return JSONResponse(storage.recent_alerts())


@app.get("/api/pnl")
async def api_pnl() -> JSONResponse:
    return JSONResponse(storage.pnl_history())


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await broadcaster.register(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive; browser may send pings
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(ws)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
