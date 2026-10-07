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
