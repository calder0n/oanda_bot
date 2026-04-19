"""Plain technical indicators used by strategies. Vectorized over pandas."""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).rolling(period, min_periods=period).mean()


def donchian_high(df: pd.DataFrame, lookback: int) -> pd.Series:
    return df["high"].shift(1).rolling(lookback, min_periods=lookback).max()


def donchian_low(df: pd.DataFrame, lookback: int) -> pd.Series:
    return df["low"].shift(1).rolling(lookback, min_periods=lookback).min()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def slope(series: pd.Series, period: int) -> pd.Series:
    """Simple OLS slope over the last `period` points, rolled."""
    x = np.arange(period)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _fit(window: np.ndarray) -> float:
        y_mean = window.mean()
        return float(((x - x_mean) * (window - y_mean)).sum() / x_var)

    return series.rolling(period, min_periods=period).apply(_fit, raw=True)


# --- Fibonacci helpers -----------------------------------------------------


FIB_RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786)
FIB_EXTENSIONS = (1.0, 1.272, 1.414, 1.618, 2.0)


def fib_retracements(swing_low: float, swing_high: float) -> dict[str, float]:
    """Standard Fibonacci retracement prices for an up-leg (low -> high).

    For a down-leg, swap the args (pass swing_high as low and vice versa); the
    returned levels then sit between the swing high and the next pullback up.
    """
    rng = swing_high - swing_low
    return {f"fib_{int(r * 1000):03d}": swing_high - r * rng for r in FIB_RETRACEMENTS}


def fib_extensions(swing_low: float, swing_high: float) -> dict[str, float]:
    """Extension prices projected past the swing in the leg's direction."""
    rng = swing_high - swing_low
    return {f"ext_{int(e * 1000):04d}": swing_low + e * rng for e in FIB_EXTENSIONS}


def latest_swing(df: pd.DataFrame, lookback: int, uptrend: bool) -> tuple[float, float, int, int] | None:
    """Locate the most recent swing leg over the last `lookback` closed bars.

    For an uptrend we anchor at the lookback-window's lowest low and take the
    highest high *after* that low. Reversed for a downtrend. Returns
    (swing_low, swing_high, low_idx, high_idx) in absolute DataFrame positions,
    or None if there are insufficient bars.
    """
    if len(df) < lookback:
        return None
    window = df.iloc[-lookback:]
    highs = window["high"].to_numpy()
    lows = window["low"].to_numpy()
    base = len(df) - lookback

    if uptrend:
        low_rel = int(np.argmin(lows))
        if low_rel >= len(highs) - 1:
            return None
        high_rel = low_rel + 1 + int(np.argmax(highs[low_rel + 1 :]))
        return float(lows[low_rel]), float(highs[high_rel]), base + low_rel, base + high_rel

    high_rel = int(np.argmax(highs))
    if high_rel >= len(lows) - 1:
        return None
    low_rel = high_rel + 1 + int(np.argmin(lows[high_rel + 1 :]))
    return float(lows[low_rel]), float(highs[high_rel]), base + low_rel, base + high_rel
