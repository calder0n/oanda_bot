"""SQLite-backed trade registry.

The strategy bot writes one row per opened trade. A separate "closer" bot
can read the same DB, watch OANDA for the current price and, when the
opportunity meets its own rules, close trades early. Keeping the schema
here (and letting SQLite serialize concurrent writers via WAL) means both
processes can share the file on the same volume with no extra machinery.

Schema
------
CREATE TABLE trades (
    trade_id         TEXT PRIMARY KEY,   -- OANDA trade id (or client id fallback)
    account_name     TEXT NOT NULL,      -- name from accounts.yaml
    account_id       TEXT NOT NULL,      -- OANDA account id
    instrument       TEXT NOT NULL,
    side             TEXT NOT NULL,      -- 'LONG' | 'SHORT'
    units            INTEGER NOT NULL,   -- signed
    entry_price      REAL NOT NULL,      -- price the bot opened at
    stop_loss_price  REAL,               -- SL price attached to the order
    take_profit_price REAL,              -- TP price attached to the order
    strategy         TEXT NOT NULL,
    reason           TEXT,
    opened_at        TEXT NOT NULL,      -- ISO-8601 UTC
    closed_at        TEXT,               -- ISO-8601 UTC or NULL if still open
    exit_price       REAL,               -- fill price when closed
    close_reason     TEXT,               -- 'tp' | 'sl' | 'time_exit' | 'manual' | 'closer_bot'
    realized_pl      REAL,               -- OANDA realized P&L in account currency
    extra_json       TEXT                -- free-form JSON blob (indicator values, etc.)
);
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id            TEXT PRIMARY KEY,
    account_name        TEXT NOT NULL,
    account_id          TEXT NOT NULL,
    instrument          TEXT NOT NULL,
    side                TEXT NOT NULL,
    units               INTEGER NOT NULL,
    entry_price         REAL NOT NULL,
    stop_loss_price     REAL,
    take_profit_price   REAL,
    strategy            TEXT NOT NULL,
    reason              TEXT,
    opened_at           TEXT NOT NULL,
    closed_at           TEXT,
    exit_price          REAL,
    close_reason        TEXT,
    realized_pl         REAL,
    extra_json          TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(account_name, closed_at);
CREATE INDEX IF NOT EXISTS idx_trades_instrument ON trades(instrument, closed_at);
"""


@dataclass
class TradeRecord:
    trade_id: str
    account_name: str
    account_id: str
    instrument: str
    side: str
    units: int
    entry_price: float
    stop_loss_price: float | None
    take_profit_price: float | None
    strategy: str
    reason: str
    opened_at: datetime
    extra: dict[str, Any] | None = None


class TradeRegistry:
    """Thread-safe SQLite wrapper. Uses WAL so a sibling reader (closer bot)
    can query the DB while this process is writing."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            self.path, timeout=30, isolation_level=None, check_same_thread=False
        )
        try:
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    def record_open(self, rec: TradeRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO trades (
                    trade_id, account_name, account_id, instrument, side, units,
                    entry_price, stop_loss_price, take_profit_price, strategy,
                    reason, opened_at, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.trade_id,
                    rec.account_name,
                    rec.account_id,
                    rec.instrument,
                    rec.side,
                    int(rec.units),
                    float(rec.entry_price),
                    None if rec.stop_loss_price is None else float(rec.stop_loss_price),
                    None if rec.take_profit_price is None else float(rec.take_profit_price),
                    rec.strategy,
                    rec.reason,
                    rec.opened_at.astimezone(timezone.utc).isoformat(),
                    json.dumps(rec.extra or {}, default=str),
                ),
            )

    def mark_closed(
        self,
        trade_id: str,
        exit_price: float | None,
        realized_pl: float | None,
        close_reason: str,
        closed_at: datetime | None = None,
    ) -> None:
        ts = (closed_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE trades
                SET closed_at = ?, exit_price = ?, realized_pl = ?, close_reason = ?
                WHERE trade_id = ? AND closed_at IS NULL
                """,
                (
                    ts,
                    None if exit_price is None else float(exit_price),
                    None if realized_pl is None else float(realized_pl),
                    close_reason,
                    trade_id,
                ),
            )

    def open_trades(self, account_name: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if account_name:
                cur = conn.execute(
                    "SELECT * FROM trades WHERE closed_at IS NULL AND account_name = ?",
                    (account_name,),
                )
            else:
                cur = conn.execute("SELECT * FROM trades WHERE closed_at IS NULL")
            return [dict(row) for row in cur.fetchall()]

    def get(self, trade_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM trades WHERE trade_id = ?", (trade_id,))
            row = cur.fetchone()
            return dict(row) if row else None
