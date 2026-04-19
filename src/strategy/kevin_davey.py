"""Kevin Davey-style volatility-filtered breakout strategy.

Kevin Davey, author of "Building Winning Algorithmic Trading Systems", is known
less for one specific formula than for a *methodology*:

    idea -> in-sample test -> out-of-sample test -> Monte Carlo -> incubation

A recurring family of ideas in his public material is the volatility-filtered
channel breakout with ATR-based stops, an R-multiple target, and a hard
time-based exit. This module is a faithful, minimal implementation of that
archetype so it can be Monte-Carlo-tested, walk-forward-optimized, and
incubated on a demo account per Davey's own playbook.

Rules
-----
Entry (next bar at market):
    * Long  if close of last closed bar > Donchian-high(lookback) AND
      ATR/price >= volatility_filter AND inside trading window.
    * Short if close < Donchian-low(lookback) AND the same filters.

Exit (attached to the order as SL/TP, plus time-exit in the runner):
    * Initial stop   = entry -/+ atr_stop_mult * ATR
    * Profit target  = entry +/- atr_target_mult * ATR
    * Time exit      = flat after time_exit_bars if neither hit
"""
from __future__ import annotations

from datetime import time

import pandas as pd

from ..indicators.technical import atr, donchian_high, donchian_low
from .base import Signal, SignalType, Strategy


class KevinDaveyBreakout(Strategy):
    name = "kevin_davey_breakout"

    def __init__(
        self,
        lookback_bars: int = 40,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 4.0,
        volatility_filter_atr_pct: float = 0.0003,
        time_exit_bars: int = 48,
        trading_window_utc: dict | None = None,
        **_: object,
    ) -> None:
        self.lookback = int(lookback_bars)
        self.atr_period = int(atr_period)
        self.stop_mult = float(atr_stop_mult)
        self.target_mult = float(atr_target_mult)
        self.vol_filter = float(volatility_filter_atr_pct)
        self.time_exit_bars = int(time_exit_bars)
        tw = trading_window_utc or {}
        self._win_start = _parse_hm(tw.get("start", "00:00"))
        self._win_end = _parse_hm(tw.get("end", "23:59"))

    def required_bars(self) -> int:
        return max(self.lookback, self.atr_period) + 5

    def evaluate(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.required_bars():
            return Signal(SignalType.NONE, reason="warmup")

        closed = df[df["complete"]] if "complete" in df.columns else df
        if len(closed) < self.required_bars():
            return Signal(SignalType.NONE, reason="warmup")

        last = closed.iloc[-1]
        last_ts: pd.Timestamp = closed.index[-1]  # type: ignore[assignment]

        if not self._in_trading_window(last_ts):
            return Signal(SignalType.NONE, reason="outside_trading_window")

        _atr = atr(closed, self.atr_period).iloc[-1]
        if pd.isna(_atr) or _atr <= 0:
            return Signal(SignalType.NONE, reason="no_atr")

        price = float(last["close"])
        if price <= 0 or (_atr / price) < self.vol_filter:
            return Signal(SignalType.NONE, reason="low_volatility")

        d_high = donchian_high(closed, self.lookback).iloc[-1]
        d_low = donchian_low(closed, self.lookback).iloc[-1]
        if pd.isna(d_high) or pd.isna(d_low):
            return Signal(SignalType.NONE, reason="warmup")

        if price > d_high:
            stop = price - self.stop_mult * _atr
            target = price + self.target_mult * _atr
            return Signal(SignalType.LONG, price, stop, target, "breakout_high")

        if price < d_low:
            stop = price + self.stop_mult * _atr
            target = price - self.target_mult * _atr
            return Signal(SignalType.SHORT, price, stop, target, "breakout_low")

        return Signal(SignalType.NONE, reason="no_breakout")

    def _in_trading_window(self, ts: pd.Timestamp) -> bool:
        t = ts.tz_convert("UTC").time() if ts.tzinfo else ts.time()
        if self._win_start <= self._win_end:
            return self._win_start <= t <= self._win_end
        return t >= self._win_start or t <= self._win_end


def _parse_hm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))
