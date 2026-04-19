"""Fibonacci day-trading strategy.

Trades pullbacks to the Fibonacci "golden zone" (38.2%-61.8%) of the most
recent intraday swing, in the direction of that swing.

The most recent leg's direction is derived from the *structure* of the last
`swing_lookback` bars: if the highest high arrived after the lowest low, we
are in an up-leg and look for long pullbacks; the opposite for shorts. An EMA
slope filter rejects setups where the trend EMA is moving against the leg.

Entry
-----
* Long: up-leg AND price retraced into [fib_618, fib_382] of the swing AND
  the last closed bar is bullish (close > open) AND EMA slope ≥ 0.
* Short: down-leg AND price retraced into [fib_382, fib_618] of the swing AND
  the last closed bar is bearish (close < open) AND EMA slope ≤ 0.

Exits (attached to the broker order)
------------------------------------
* Stop:   just past the 78.6 % retracement (invalidation of the swing).
* Target: 1.272 Fibonacci extension of the swing.

Risk
----
A separate per-tick time-based exit (`time_exit_bars`) caps how long any
trade can sit open, since this is a *day*-trading strategy. With the default
$1,000 starting balance and 1 % risk_per_trade, each trade risks ~$10 and the
runner caps daily loss at 3 %.
"""
from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

from ..indicators.technical import atr, ema, fib_extensions, fib_retracements
from .base import Signal, SignalType, Strategy


class FibonacciDayTrading(Strategy):
    name = "fibonacci_day_trading"

    def __init__(
        self,
        swing_lookback: int = 30,
        trend_ema_period: int = 50,
        atr_period: int = 14,
        entry_zone_low: float = 0.382,
        entry_zone_high: float = 0.618,
        invalidation_fib: float = 0.786,
        target_extension: float = 1.272,
        min_atr_pct: float = 0.0002,
        time_exit_bars: int = 24,
        trading_window_utc: dict | None = None,
        **_: object,
    ) -> None:
        self.swing_lookback = int(swing_lookback)
        self.trend_ema = int(trend_ema_period)
        self.atr_period = int(atr_period)
        # entry_zone_low is the *shallower* retracement (closer to the swing
        # extreme); entry_zone_high is the *deeper* one. Swap if the user
        # supplied them in the opposite order so the band is always valid.
        lo, hi = sorted((float(entry_zone_low), float(entry_zone_high)))
        self.zone_shallow, self.zone_deep = lo, hi
        self.invalidation = float(invalidation_fib)
        self.target_extension = float(target_extension)
        self.min_atr_pct = float(min_atr_pct)
        self.time_exit_bars = int(time_exit_bars)
        tw = trading_window_utc or {}
        self._win_start = _parse_hm(tw.get("start", "00:00"))
        self._win_end = _parse_hm(tw.get("end", "23:59"))

    def required_bars(self) -> int:
        return max(self.swing_lookback, self.trend_ema, self.atr_period) + 5

    def evaluate(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.required_bars():
            return Signal(SignalType.NONE, reason="warmup")

        closed = df[df["complete"]] if "complete" in df.columns else df
        if len(closed) < self.required_bars():
            return Signal(SignalType.NONE, reason="warmup")

        last = closed.iloc[-1]
        last_ts: pd.Timestamp = closed.index[-1]  # type: ignore[assignment]
        price = float(last["close"])
        bar_open = float(last["open"])

        ema_series = ema(closed["close"], self.trend_ema)
        ema_val = float(ema_series.iloc[-1]) if not ema_series.empty else float("nan")
        ema_prev = (
            float(ema_series.iloc[-6])
            if len(ema_series) >= 6 and not pd.isna(ema_series.iloc[-6])
            else float("nan")
        )
        ema_slope = (ema_val - ema_prev) if not pd.isna(ema_prev) else float("nan")
        atr_series = atr(closed, self.atr_period)
        atr_val = float(atr_series.iloc[-1]) if not atr_series.empty else float("nan")
        atr_pct = (atr_val / price) if (price > 0 and not pd.isna(atr_val)) else float("nan")

        # Direction comes from the swing structure itself: whichever extreme
        # arrived later in the lookback window defines the active leg.
        window = closed.iloc[-self.swing_lookback :]
        highs = window["high"].to_numpy()
        lows = window["low"].to_numpy()
        hi_rel = int(np.argmax(highs))
        lo_rel = int(np.argmin(lows))
        uptrend = hi_rel > lo_rel
        swing_low = float(lows[lo_rel])
        swing_high = float(highs[hi_rel])
        rng = swing_high - swing_low

        details: dict = {
            "price": round(price, 6),
            "open": round(bar_open, 6),
            "ema": round(ema_val, 6) if not pd.isna(ema_val) else None,
            "ema_slope": round(ema_slope, 6) if not pd.isna(ema_slope) else None,
            "trend": "up" if uptrend else "down",
            "atr": round(atr_val, 6) if not pd.isna(atr_val) else None,
            "atr_pct": round(atr_pct, 6) if not pd.isna(atr_pct) else None,
            "min_atr_pct": self.min_atr_pct,
            "swing_lookback": self.swing_lookback,
            "swing_low": round(swing_low, 6),
            "swing_high": round(swing_high, 6),
            "swing_range": round(rng, 6),
            "bar_time": last_ts.isoformat() if hasattr(last_ts, "isoformat") else str(last_ts),
        }

        if not self._in_trading_window(last_ts):
            return Signal(SignalType.NONE, reason="outside_trading_window", details=details)
        if pd.isna(atr_val) or atr_val <= 0:
            return Signal(SignalType.NONE, reason="no_atr", details=details)
        if pd.isna(ema_val):
            return Signal(SignalType.NONE, reason="warmup", details=details)
        if price <= 0 or atr_pct < self.min_atr_pct:
            return Signal(SignalType.NONE, reason="low_volatility", details=details)
        if rng <= 0:
            return Signal(SignalType.NONE, reason="degenerate_swing", details=details)
        if not pd.isna(ema_slope):
            if uptrend and ema_slope < 0:
                return Signal(SignalType.NONE, reason="ema_slope_against_leg", details=details)
            if (not uptrend) and ema_slope > 0:
                return Signal(SignalType.NONE, reason="ema_slope_against_leg", details=details)

        retr = fib_retracements(swing_low, swing_high)
        ext = fib_extensions(swing_low, swing_high)
        details.update(
            {
                **{k: round(v, 6) for k, v in retr.items()},
                **{k: round(v, 6) for k, v in ext.items()},
                "low_idx": int(lo_rel),
                "high_idx": int(hi_rel),
                "zone_shallow": self.zone_shallow,
                "zone_deep": self.zone_deep,
            }
        )

        # zone bounds in price space
        shallow_price = swing_high - self.zone_shallow * rng if uptrend else swing_low + self.zone_shallow * rng
        deep_price = swing_high - self.zone_deep * rng if uptrend else swing_low + self.zone_deep * rng
        zone_lo, zone_hi = sorted((shallow_price, deep_price))
        details["zone_low_price"] = round(zone_lo, 6)
        details["zone_high_price"] = round(zone_hi, 6)

        in_zone = zone_lo <= price <= zone_hi
        details["in_zone"] = in_zone
        if not in_zone:
            return Signal(SignalType.NONE, reason="not_in_fib_zone", details=details)

        if uptrend:
            # require a bullish confirmation candle
            if price <= bar_open:
                return Signal(SignalType.NONE, reason="awaiting_bull_confirmation", details=details)
            stop = swing_high - self.invalidation * rng
            target = swing_low + self.target_extension * rng
            if stop >= price or target <= price:
                return Signal(SignalType.NONE, reason="invalid_levels", details=details)
            details["stop"] = round(stop, 6)
            details["target"] = round(target, 6)
            return Signal(SignalType.LONG, price, stop, target, "fib_pullback_long", details)

        # downtrend
        if price >= bar_open:
            return Signal(SignalType.NONE, reason="awaiting_bear_confirmation", details=details)
        stop = swing_low + self.invalidation * rng
        target = swing_high - self.target_extension * rng
        if stop <= price or target >= price:
            return Signal(SignalType.NONE, reason="invalid_levels", details=details)
        details["stop"] = round(stop, 6)
        details["target"] = round(target, 6)
        return Signal(SignalType.SHORT, price, stop, target, "fib_pullback_short", details)

    def _in_trading_window(self, ts: pd.Timestamp) -> bool:
        t = ts.tz_convert("UTC").time() if ts.tzinfo else ts.time()
        if self._win_start <= self._win_end:
            return self._win_start <= t <= self._win_end
        return t >= self._win_start or t <= self._win_end


def _parse_hm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))
