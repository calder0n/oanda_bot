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


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's RSI on the close series."""
    close = df["close"]
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder_smooth(gain, period)
    avg_loss = _wilder_smooth(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss != 0.0, 100.0)
    return out


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ADX. Returns a series aligned with df's index."""
    high = df["high"]
    low = df["low"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
        index=df.index,
    )

    tr = true_range(df)
    atr_w = _wilder_smooth(tr, period)
    plus_di = 100.0 * _wilder_smooth(plus_dm, period) / atr_w.replace(0.0, np.nan)
    minus_di = 100.0 * _wilder_smooth(minus_dm, period) / atr_w.replace(0.0, np.nan)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return _wilder_smooth(dx.fillna(0.0), period)
