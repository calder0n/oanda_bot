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
