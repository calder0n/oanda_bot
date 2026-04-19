"""Append-only JSON registry of opened trades.

Each worker writes to `state/trades_<account>.json`. Entries are intended to
be read by a separate watcher/closer process that decides when to close
positions by inspecting live prices vs. the recorded TP/SL.

Shape:

    {
      "account": "demo-primary",
      "trades": [
        {
          "id": "12345",                # OANDA trade ID
          "client_tag": "demo-primary:scalping_adx_rsi:17132...",
          "account": "demo-primary",
          "instrument": "EUR_USD",
          "side": "LONG",
          "units": 1200,
          "entry_price": 1.0841,
          "stop_loss": 1.0821,
          "take_profit": 1.0861,
          "sell_price": null,           # filled when the watcher closes it
          "status": "open",             # open | closed
          "opened_at": "2025-01-01T12:34:56+00:00",
          "closed_at": null,
          "reason": "adx_strong+rsi_oversold",
          "strategy": "scalping_adx_rsi",
          "indicators": {"adx": 28.4, "rsi": 32.1, "atr": 0.00031}
        }
      ]
    }
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TradeRegistry:
    account: str
    path: Path
    _lock: asyncio.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        if not self.path.exists():
            self._write({"account": self.account, "trades": []})

    # --- low-level I/O ---------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text() or "{}") or {
                "account": self.account,
                "trades": [],
            }
        except json.JSONDecodeError:
            return {"account": self.account, "trades": []}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = tempfile.NamedTemporaryFile(
            "w",
            dir=str(self.path.parent),
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            json.dump(data, tmp, indent=2, sort_keys=False, default=str)
            tmp.flush()
            os.fsync(tmp.fileno())
        finally:
            tmp.close()
        os.replace(tmp.name, self.path)

    # --- public API ------------------------------------------------------

    async def record_open(
        self,
        trade_id: str,
        instrument: str,
        side: str,
        units: int,
        entry_price: float | None,
        stop_loss: float | None,
        take_profit: float | None,
        reason: str,
        strategy: str,
        client_tag: str,
        indicators: dict | None = None,
    ) -> dict:
        record = {
            "id": str(trade_id),
            "client_tag": client_tag,
            "account": self.account,
            "instrument": instrument,
            "side": side,
            "units": int(units),
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "sell_price": None,
            "status": "open",
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "closed_at": None,
            "reason": reason,
            "strategy": strategy,
            "indicators": indicators or {},
        }
        async with self._lock:
            data = self._read()
            data["trades"].append(record)
            self._write(data)
        return record

    async def mark_closed(
        self,
        trade_id: str,
        sell_price: float | None,
        close_reason: str | None = None,
    ) -> dict | None:
        async with self._lock:
            data = self._read()
            for t in data["trades"]:
                if str(t.get("id")) == str(trade_id) and t.get("status") == "open":
                    t["status"] = "closed"
                    t["sell_price"] = sell_price
                    t["closed_at"] = datetime.now(timezone.utc).isoformat()
                    if close_reason:
                        t["close_reason"] = close_reason
                    self._write(data)
                    return t
        return None

    async def list_open(self) -> list[dict]:
        async with self._lock:
            data = self._read()
        return [t for t in data["trades"] if t.get("status") == "open"]
