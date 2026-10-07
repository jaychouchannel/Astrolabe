import time

from astrolabe.alert_engine import Alert
from astrolabe.storage import Storage


def test_storage_roundtrip(tmp_path):
    st = Storage(str(tmp_path / "t.db"))
    now = time.time()
    st.record_price("BTC-USDT", 100.0, now - 10)
    st.record_price("BTC-USDT", 101.0, now)
    assert st.price_history("BTC-USDT") == [(now - 10, 100.0), (now, 101.0)]

    st.record_snapshot({"equity": 5000.0})
    st.record_alerts([Alert("price_move", "BTC-USDT", "msg", "warn")])
    alerts = st.recent_alerts()
    assert alerts[0]["type"] == "price_move"

    pnl = st.pnl_history()
    assert len(pnl) == 1 and pnl[0][1] == 5000.0
    st.close()


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
