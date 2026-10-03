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
