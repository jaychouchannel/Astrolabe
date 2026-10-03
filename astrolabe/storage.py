"""SQLite persistence: price history, account snapshots, alert history."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class Storage:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # all writes flow through the event loop's thread; TestClient uses
        # a worker thread, so relax sqlite's same-thread guard
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS prices (
                inst_id TEXT NOT NULL, ts REAL NOT NULL, last REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_prices_inst_ts ON prices(inst_id, ts);
            CREATE TABLE IF NOT EXISTS snapshots (
                ts REAL PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alerts (
                ts REAL NOT NULL, type TEXT NOT NULL,
                inst_id TEXT NOT NULL, message TEXT NOT NULL, severity TEXT NOT NULL
            );
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ---- writes ----
    def record_price(self, inst_id: str, last: float, ts: float | None = None) -> None:
        self._db.execute(
            "INSERT INTO prices(inst_id, ts, last) VALUES (?, ?, ?)",
            (inst_id, ts or time.time(), last),
        )
        self._db.commit()

    def record_snapshot(self, payload: dict) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO snapshots(ts, payload) VALUES (?, ?)",
            (time.time(), json.dumps(payload, ensure_ascii=False)),
        )
        self._db.commit()

    def record_alerts(self, alerts: list) -> None:
        for a in alerts:
            self._db.execute(
                "INSERT INTO alerts(ts, type, inst_id, message, severity) VALUES (?, ?, ?, ?, ?)",
                (a.ts, a.type, a.inst_id, a.message, a.severity),
            )
        self._db.commit()

    # ---- reads ----
    def price_history(self, inst_id: str, limit: int = 500) -> list[tuple[float, float]]:
        rows = self._db.execute(
            "SELECT ts, last FROM prices WHERE inst_id = ? ORDER BY ts DESC LIMIT ?",
            (inst_id, limit),
        ).fetchall()
        return list(reversed(rows))

    def recent_alerts(self, limit: int = 50) -> list[dict]:
        rows = self._db.execute(
            "SELECT ts, type, inst_id, message, severity FROM alerts "
            "ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"ts": r[0], "type": r[1], "instId": r[2], "message": r[3], "severity": r[4]}
            for r in rows
        ]

    def pnl_history(self, limit: int = 500) -> list[tuple[float, float]]:
        """Extract total equity (USDT) from stored account snapshots."""
        rows = self._db.execute(
            "SELECT ts, payload FROM snapshots ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        out: list[tuple[float, float]] = []
        for ts, payload in reversed(rows):
            try:
                eq = float(json.loads(payload).get("equity") or 0)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if eq > 0:
                out.append((ts, eq))
        return out
