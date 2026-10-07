"""Load settings from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader; real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #")[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "")
    items = [x.strip() for x in raw.split(",") if x.strip()]
    return items or default


@dataclass
class Settings:
    # OKX credentials (empty => public-data-only mode)
    api_key: str = ""
    secret_key: str = ""
    passphrase: str = ""
    simulated: bool = True

    # instruments: spot + USDT-margined perpetuals
    spot_instruments: list[str] = field(
        default_factory=lambda: ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    )
    swap_instruments: list[str] = field(
        default_factory=lambda: ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    )
    default_chart_inst: str = "BTC-USDT"
    chart_timeframes: list[str] = field(
        default_factory=lambda: ["1m", "5m", "15m", "1H", "4H", "1D", "1W", "1M"]
    )

    # alert thresholds
    price_change_pct: float = 2.0      # % move within window triggers alert
    price_change_window_s: int = 300   # 5 minutes
    funding_rate_pct: float = 0.1      # abs funding rate threshold, %
    alert_cooldown_s: int = 600        # per (rule, instrument) cooldown

    # telegram
    tg_bot_token: str = ""
    tg_chat_id: str = ""

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

    # server
    host: str = "127.0.0.1"
    port: int = 8765
    db_path: str = "astrolabe.db"

    @property
    def has_private_access(self) -> bool:
        return bool(self.api_key and self.secret_key and self.passphrase)

    @property
    def has_telegram(self) -> bool:
        return bool(self.tg_bot_token and self.tg_chat_id)

    @property
    def watchlist(self) -> list[str]:
        return self.spot_instruments + self.swap_instruments


def load_settings(env_file: Path | None = None) -> Settings:
    if env_file is None:
        env_file = Path(os.environ.get("ASTROLABE_ENV", ".env"))
    _load_dotenv(env_file)

    return Settings(
        api_key=os.environ.get("OKX_API_KEY", ""),
        secret_key=os.environ.get("OKX_SECRET_KEY", ""),
        passphrase=os.environ.get("OKX_PASSPHRASE", ""),
        simulated=os.environ.get("OKX_SIMULATED", "1") not in ("0", "false", "False"),
        spot_instruments=_env_list("SPOT_INSTRUMENTS", ["BTC-USDT", "ETH-USDT", "SOL-USDT"]),
        swap_instruments=_env_list(
            "SWAP_INSTRUMENTS",
            ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
        ),
        default_chart_inst=os.environ.get("DEFAULT_CHART_INST", "BTC-USDT"),
        chart_timeframes=_env_list(
            "CHART_TIMEFRAMES",
            ["1m", "5m", "15m", "1H", "4H", "1D", "1W", "1M"],
        ),
        price_change_pct=float(os.environ.get("ALERT_PRICE_CHANGE_PCT", "2.0")),
        price_change_window_s=int(os.environ.get("ALERT_PRICE_WINDOW_S", "300")),
        funding_rate_pct=float(os.environ.get("ALERT_FUNDING_PCT", "0.1")),
        alert_cooldown_s=int(os.environ.get("ALERT_COOLDOWN_S", "600")),
        tg_bot_token=os.environ.get("TG_BOT_TOKEN", ""),
        tg_chat_id=os.environ.get("TG_CHAT_ID", ""),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8765")),
        trading_enabled=os.environ.get("OKX_TRADING_ENABLED", "0") in ("1", "true", "True"),
        strategy_enabled=os.environ.get("STRATEGY_ENABLED", "0") in ("1", "true", "True"),
        allow_live=os.environ.get("STRATEGY_ALLOW_LIVE", "0") in ("1", "true", "True"),
        strategy_inst=os.environ.get("STRATEGY_INST", "BTC-USDT-SWAP"),
        strategy_bar=os.environ.get("STRATEGY_BAR", "1m"),
        strategy_size_usdt=float(os.environ.get("STRATEGY_SIZE_USDT", "100")),
        strategy_lever=int(os.environ.get("STRATEGY_LEVER", "3")),
        strategy_poll_s=int(os.environ.get("STRATEGY_POLL_S", "60")),
        strategy_cooldown_s=int(os.environ.get("STRATEGY_COOLDOWN_S", "300")),
        db_path=os.environ.get("DB_PATH", "astrolabe.db"),
    )
