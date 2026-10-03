import time

import pytest

from astrolabe.alert_engine import AlertEngine
from astrolabe.config import Settings


@pytest.fixture
def engine():
    return AlertEngine(Settings())


def test_price_move_alerts_and_cooldowns(engine):
    now = time.time()
    # baseline
    assert engine.on_ticker("BTC-USDT", 100.0, now - 300) == []
    # +3% within window (threshold 2%) -> alert
    alerts = engine.on_ticker("BTC-USDT", 103.0, now)
    assert len(alerts) == 1
    assert alerts[0].type == "price_move"
    assert "🚀" in alerts[0].message
    # cooldown suppresses immediate repeat
    assert engine.on_ticker("BTC-USDT", 106.0, now + 1) == []
    # old points fall out of window -> no alert at same level
    engine2 = AlertEngine(Settings(price_change_pct=10.0))
    engine2.on_ticker("BTC-USDT", 100.0, now - 400)
    assert engine2.on_ticker("BTC-USDT", 101.0, now) == []


def test_price_drop_alert(engine):
    now = time.time()
    engine.on_ticker("ETH-USDT", 2000.0, now - 60)
    alerts = engine.on_ticker("ETH-USDT", 1950.0, now)  # -2.5%
    assert len(alerts) == 1
    assert "🌑" in alerts[0].message


def test_funding_rate_first_observation_silent(engine):
    assert engine.on_funding_rate("BTC-USDT-SWAP", 0.15) == []
    alerts = engine.on_funding_rate("BTC-USDT-SWAP", 0.2)
    assert len(alerts) == 1
    assert alerts[0].type == "funding_rate"


def test_funding_rate_recovery_no_alert(engine):
    engine.on_funding_rate("BTC-USDT-SWAP", 0.05)
    assert engine.on_funding_rate("BTC-USDT-SWAP", 0.06) == []


def test_position_open_and_close(engine):
    alerts = engine.on_positions([{"instId": "BTC-USDT-SWAP", "pos": "1.5"}])
    assert len(alerts) == 1 and "新开" in alerts[0].message
    # same position repeated -> silent
    assert engine.on_positions([{"instId": "BTC-USDT-SWAP", "pos": "1.5"}]) == []
    # closed -> alert
    alerts = engine.on_positions([])
    assert len(alerts) == 1 and "平仓" in alerts[0].message


def test_balance_change(engine):
    assert engine.on_balance(1000.0) == []
    alerts = engine.on_balance(1030.0)  # +3%
    assert len(alerts) == 1 and alerts[0].type == "balance_change"
