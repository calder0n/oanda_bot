"""ADX + RSI scalping strategy (M1 by default).

Rules (evaluated on the most recent CLOSED candle):

    BUY  when ADX >= adx_min AND RSI <= rsi_oversold
    SELL when ADX >= adx_min AND RSI >= rsi_overbought

Defaults match the spec:
    adx_min            = 23
    rsi_oversold       = 35
    rsi_overbought     = 65

Stops/targets are ATR-based so the runner's risk manager can size positions
correctly against a small ($1,000) starting balance.
"""
from __future__ import annotations

import pandas as pd

from ..indicators.technical import adx, atr, rsi
from .base import Signal, SignalType, Strategy


class ScalpingAdxRsi(Strategy):
    name = "scalping_adx_rsi"

    def __init__(
        self,
        adx_period: int = 14,
        rsi_period: int = 14,
        atr_period: int = 14,
        adx_min: float = 23.0,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 65.0,
        atr_stop_mult: float = 1.5,
        atr_target_mult: float = 1.5,
        **_: object,
    ) -> None:
        self.adx_period = int(adx_period)
        self.rsi_period = int(rsi_period)
        self.atr_period = int(atr_period)
        self.adx_min = float(adx_min)
        self.rsi_oversold = float(rsi_oversold)
        self.rsi_overbought = float(rsi_overbought)
        self.stop_mult = float(atr_stop_mult)
        self.target_mult = float(atr_target_mult)

    def required_bars(self) -> int:
        # Wilder smoothing needs ~2x the period to stabilize on a scalping tf.
        return max(self.adx_period, self.rsi_period, self.atr_period) * 3 + 5

    def evaluate(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.required_bars():
            return Signal(SignalType.NONE, reason="warmup")

        closed = df[df["complete"]] if "complete" in df.columns else df
        if len(closed) < self.required_bars():
            return Signal(SignalType.NONE, reason="warmup")

        last = closed.iloc[-1]
        last_ts: pd.Timestamp = closed.index[-1]  # type: ignore[assignment]
        price = float(last["close"])

        adx_series = adx(closed, self.adx_period)
        rsi_series = rsi(closed, self.rsi_period)
        atr_series = atr(closed, self.atr_period)

        adx_val = float(adx_series.iloc[-1]) if not adx_series.empty else float("nan")
        rsi_val = float(rsi_series.iloc[-1]) if not rsi_series.empty else float("nan")
        atr_val = float(atr_series.iloc[-1]) if not atr_series.empty else float("nan")

        details = {
            "price": round(price, 6),
            "adx": round(adx_val, 4) if pd.notna(adx_val) else None,
            "rsi": round(rsi_val, 4) if pd.notna(rsi_val) else None,
            "atr": round(atr_val, 6) if pd.notna(atr_val) else None,
            "adx_min": self.adx_min,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "bar_time": last_ts.isoformat() if hasattr(last_ts, "isoformat") else str(last_ts),
        }

        if pd.isna(adx_val) or pd.isna(rsi_val) or pd.isna(atr_val) or atr_val <= 0:
            return Signal(SignalType.NONE, reason="warmup", details=details)

        adx_ok = adx_val >= self.adx_min

        if adx_ok and rsi_val <= self.rsi_oversold:
            stop = price - self.stop_mult * atr_val
            target = price + self.target_mult * atr_val
            details["stop"] = round(stop, 6)
            details["target"] = round(target, 6)
            return Signal(
                SignalType.LONG, price, stop, target, "adx_strong+rsi_oversold", details
            )

        if adx_ok and rsi_val >= self.rsi_overbought:
            stop = price + self.stop_mult * atr_val
            target = price - self.target_mult * atr_val
            details["stop"] = round(stop, 6)
            details["target"] = round(target, 6)
            return Signal(
                SignalType.SHORT, price, stop, target, "adx_strong+rsi_overbought", details
            )

        if not adx_ok:
            reason = "adx_below_threshold"
        elif rsi_val < self.rsi_overbought and rsi_val > self.rsi_oversold:
            reason = "rsi_neutral"
        else:
            reason = "no_signal"
        return Signal(SignalType.NONE, reason=reason, details=details)
