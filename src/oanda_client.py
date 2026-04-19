"""Thin async-friendly wrapper around oandapyV20 with retries."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pandas as pd
from oandapyV20 import API
from oandapyV20.endpoints import accounts, instruments, orders, positions, pricing, trades
from oandapyV20.exceptions import V20Error
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class Candle:
    time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: int
    complete: bool


class OandaClient:
    """Synchronous OANDA REST client exposed through async helpers."""

    def __init__(self, access_token: str, account_id: str, environment: str = "practice"):
        self._api = API(access_token=access_token, environment=environment)
        self.account_id = account_id
        self.environment = environment

    async def _call(self, req: Any) -> Any:
        return await asyncio.to_thread(self._request_with_retry, req)

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        retry=retry_if_exception_type((V20Error, OSError)),
    )
    def _request_with_retry(self, req: Any) -> Any:
        return self._api.request(req)

    # --- account ---------------------------------------------------------

    async def account_summary(self) -> dict:
        r = accounts.AccountSummary(accountID=self.account_id)
        return (await self._call(r))["account"]

    async def tradeable_instruments(self) -> list[dict]:
        r = accounts.AccountInstruments(accountID=self.account_id)
        return (await self._call(r))["instruments"]

    # --- market data -----------------------------------------------------

    async def candles(
        self, instrument: str, granularity: str = "M15", count: int = 200
    ) -> pd.DataFrame:
        params = {"granularity": granularity, "count": count, "price": "M"}
        r = instruments.InstrumentsCandles(instrument=instrument, params=params)
        raw = (await self._call(r)).get("candles", [])
        rows = []
        for c in raw:
            mid = c["mid"]
            rows.append(
                {
                    "time": pd.to_datetime(c["time"], utc=True),
                    "open": float(mid["o"]),
                    "high": float(mid["h"]),
                    "low": float(mid["l"]),
                    "close": float(mid["c"]),
                    "volume": int(c["volume"]),
                    "complete": bool(c["complete"]),
                }
            )
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.set_index("time").sort_index()
        return df

    async def current_price(self, instrument: str) -> tuple[float, float]:
        params = {"instruments": instrument}
        r = pricing.PricingInfo(accountID=self.account_id, params=params)
        resp = await self._call(r)
        p = resp["prices"][0]
        bid = float(p["bids"][0]["price"])
        ask = float(p["asks"][0]["price"])
        return bid, ask

    # --- orders / positions ---------------------------------------------

    async def open_positions(self) -> list[dict]:
        r = positions.OpenPositions(accountID=self.account_id)
        return (await self._call(r)).get("positions", [])

    async def open_trades(self) -> list[dict]:
        r = trades.OpenTrades(accountID=self.account_id)
        return (await self._call(r)).get("trades", [])

    async def market_order(
        self,
        instrument: str,
        units: int,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
        client_tag: str | None = None,
    ) -> dict:
        order: dict[str, Any] = {
            "order": {
                "instrument": instrument,
                "units": str(units),
                "type": "MARKET",
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }
        if stop_loss_price is not None:
            order["order"]["stopLossOnFill"] = {"price": f"{stop_loss_price:.5f}"}
        if take_profit_price is not None:
            order["order"]["takeProfitOnFill"] = {"price": f"{take_profit_price:.5f}"}
        if client_tag:
            order["order"]["clientExtensions"] = {"tag": client_tag, "id": client_tag}

        r = orders.OrderCreate(accountID=self.account_id, data=order)
        return await self._call(r)

    async def close_trade(self, trade_id: str, units: str = "ALL") -> dict:
        r = trades.TradeClose(accountID=self.account_id, tradeID=trade_id, data={"units": units})
        return await self._call(r)
