"""Sergey Magala-style trading strategy using the FOTSI composite indicator.

Rules (evaluated on each *closed* candle):

Entry LONG when:
    fotsi >= entry_threshold
    trend > 0   AND   strength > strength_min
    momentum was < 0 within the last `pullback_lookback` bars  (buy pullbacks)
    price above ema_fast (session bias confirmation)

Entry SHORT when the mirror image is true (fotsi <= -entry_threshold, etc.).

Exits are attached to the OANDA order as stop-loss and take-profit:
    stop  = entry -/+ atr_stop_mult * ATR
    target = entry +/- atr_target_mult * ATR

Position sizing is delegated to `risk.manager.compute_position_units`, which
converts `risk_per_trade_pct` of NAV into a signed unit count using the stop
distance. On a $1,000 demo account with `risk_per_trade_pct = 1.0`, each trade
risks roughly $10 - inside the $30 total-at-risk budget with 3 simultaneous
positions.
"""
from __future__ import annotations

from datetime import time

import pandas as pd

from ..indicators.fotsi import FotsiParams, fotsi, required_warmup
from ..indicators.technical import ema
from .base import Signal, SignalType, Strategy


class MagalaFotsi(Strategy):
    name = "magala_fotsi"

    def __init__(
        self,
        ema_fast: int = 8,
        ema_slow: int = 21,
        ema_trend: int = 55,
        stoch_period: int = 14,
        stoch_smooth: int = 3,
        adx_period: int = 14,
        weight_trend: float = 0.4,
        weight_momentum: float = 0.3,
        weight_strength: float = 0.3,
        entry_threshold: float = 55.0,
        strength_min: float = 20.0,
        pullback_lookback: int = 5,
        atr_stop_mult: float = 1.5,
        atr_target_mult: float = 3.0,
        min_atr_pct: float = 0.00015,
        trading_window_utc: dict | None = None,
        **_: object,
    ) -> None:
        self.params = FotsiParams(
            ema_fast=int(ema_fast),
            ema_slow=int(ema_slow),
            ema_trend=int(ema_trend),
            stoch_period=int(stoch_period),
            stoch_smooth=int(stoch_smooth),
            adx_period=int(adx_period),
            weight_trend=float(weight_trend),
            weight_momentum=float(weight_momentum),
            weight_strength=float(weight_strength),
        )
        self.entry_threshold = float(entry_threshold)
        self.strength_min = float(strength_min)
        self.pullback_lookback = int(pullback_lookback)
        self.atr_stop_mult = float(atr_stop_mult)
        self.atr_target_mult = float(atr_target_mult)
        self.min_atr_pct = float(min_atr_pct)
        tw = trading_window_utc or {}
        self._win_start = _parse_hm(tw.get("start", "00:00"))
        self._win_end = _parse_hm(tw.get("end", "23:59"))

    def required_bars(self) -> int:
        return required_warmup(self.params) + max(self.pullback_lookback, 10)

    def evaluate(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.required_bars():
            return Signal(SignalType.NONE, reason="warmup")

        closed = df[df["complete"]] if "complete" in df.columns else df
        if len(closed) < self.required_bars():
            return Signal(SignalType.NONE, reason="warmup")

        vals = fotsi(closed, self.params)
        last = closed.iloc[-1]
        last_ts: pd.Timestamp = closed.index[-1]  # type: ignore[assignment]
        price = float(last["close"])

        ema_fast = float(ema(closed["close"], self.params.ema_fast).iloc[-1])
        f_row = vals.iloc[-1]
        recent = vals.iloc[-(self.pullback_lookback + 1) : -1]

        _atr = float(f_row["atr"]) if pd.notna(f_row["atr"]) else float("nan")
        atr_pct = (_atr / price) if (price > 0 and not pd.isna(_atr)) else float("nan")

        details = {
            "price": round(price, 6),
            "ema_fast": round(ema_fast, 6),
            "trend": _r(f_row["trend"]),
            "momentum": _r(f_row["momentum"]),
            "strength": _r(f_row["strength"]),
            "fotsi": _r(f_row["fotsi"]),
            "atr": _r(_atr),
            "atr_pct": _r(atr_pct),
            "entry_threshold": self.entry_threshold,
            "strength_min": self.strength_min,
            "bar_time": last_ts.isoformat() if hasattr(last_ts, "isoformat") else str(last_ts),
        }

        if not self._in_trading_window(last_ts):
            return Signal(SignalType.NONE, reason="outside_trading_window", details=details)
        if pd.isna(_atr) or _atr <= 0:
            return Signal(SignalType.NONE, reason="no_atr", details=details)
        if atr_pct < self.min_atr_pct:
            return Signal(SignalType.NONE, reason="low_volatility", details=details)

        fotsi_v = float(f_row["fotsi"])
        trend_v = float(f_row["trend"])
        momentum_v = float(f_row["momentum"])
        strength_v = float(f_row["strength"])

        # LONG: strong bullish composite AND recent momentum dip (buy the pullback)
        if (
            fotsi_v >= self.entry_threshold
            and trend_v > 0
            and strength_v >= self.strength_min
            and price > ema_fast
            and not recent.empty
            and (recent["momentum"] < 0).any()
        ):
            stop = price - self.atr_stop_mult * _atr
            target = price + self.atr_target_mult * _atr
            details["stop"] = _r(stop)
            details["target"] = _r(target)
            return Signal(SignalType.LONG, price, stop, target, "fotsi_long", details)

        # SHORT: mirror
        if (
            fotsi_v <= -self.entry_threshold
            and trend_v < 0
            and strength_v <= -self.strength_min
            and price < ema_fast
            and not recent.empty
            and (recent["momentum"] > 0).any()
        ):
            stop = price + self.atr_stop_mult * _atr
            target = price - self.atr_target_mult * _atr
            details["stop"] = _r(stop)
            details["target"] = _r(target)
            return Signal(SignalType.SHORT, price, stop, target, "fotsi_short", details)

        # Not strong enough
        reason = (
            "below_long_threshold"
            if fotsi_v > 0
            else "below_short_threshold"
            if fotsi_v < 0
            else "flat"
        )
        return Signal(SignalType.NONE, reason=reason, details=details)

    def _in_trading_window(self, ts: pd.Timestamp) -> bool:
        t = ts.tz_convert("UTC").time() if ts.tzinfo else ts.time()
        if self._win_start <= self._win_end:
            return self._win_start <= t <= self._win_end
        return t >= self._win_start or t <= self._win_end


def _parse_hm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def _r(x: float) -> float | None:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        return None
    return round(float(x), 6)
