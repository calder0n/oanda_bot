"""FOTSI - Fast Oscillator Trend Strength Index.

Sergey Magala popularized what is commonly referred to as the *FOTSI*
indicator: a composite score that fuses three orthogonal reads of the market
into a single number in the [-100, +100] range.

    FOTSI = w_trend * trend_score
          + w_momentum * momentum_score
          + w_strength * strength_score

Components (each is normalized to [-100, +100]):

* trend_score      derived from the relative position of price versus a fast
                   and a slow EMA. Above both EMAs, and fast > slow, gives
                   a maximally positive trend score.
* momentum_score   Stochastic %K of the close, mapped so that mid-band (50)
                   is neutral. Directional bias (rising vs falling %K) is
                   folded in so a rising %K in an uptrend confirms.
* strength_score   ADX-style trend strength scaled to +/- based on directional
                   movement, so a strong bullish trend is +100, a strong
                   bearish trend is -100, and a chop is close to 0.

The exact recipe is not public, so this is a faithful *interpretation* built
from primitives that Magala's public content emphasizes (EMA structure,
stochastic confirmation, ADX). Weights and periods are configurable so it can
be tuned against the demo account before being trusted with real money.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .technical import atr, ema, true_range


@dataclass
class FotsiParams:
    ema_fast: int = 8
    ema_slow: int = 21
    ema_trend: int = 55
    stoch_period: int = 14
    stoch_smooth: int = 3
    adx_period: int = 14
    weight_trend: float = 0.4
    weight_momentum: float = 0.3
    weight_strength: float = 0.3


def stochastic_k(df: pd.DataFrame, period: int, smooth: int) -> pd.Series:
    low_n = df["low"].rolling(period, min_periods=period).min()
    high_n = df["high"].rolling(period, min_periods=period).max()
    denom = (high_n - low_n).replace(0.0, np.nan)
    raw = 100.0 * (df["close"] - low_n) / denom
    return raw.rolling(smooth, min_periods=smooth).mean()


def adx_directional(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series]:
    """Return (signed_adx, raw_adx). Signed adx is +adx if +DI>-DI else -adx."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    tr = true_range(df).rolling(period, min_periods=period).mean()
    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).rolling(
        period, min_periods=period
    ).mean() / tr.replace(0.0, np.nan)
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).rolling(
        period, min_periods=period
    ).mean() / tr.replace(0.0, np.nan)

    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    adx_raw = dx.rolling(period, min_periods=period).mean()
    signed = adx_raw.where(plus_di >= minus_di, -adx_raw)
    return signed, adx_raw


def _trend_score(df: pd.DataFrame, p: FotsiParams) -> pd.Series:
    ema_f = ema(df["close"], p.ema_fast)
    ema_s = ema(df["close"], p.ema_slow)
    ema_t = ema(df["close"], p.ema_trend)
    price = df["close"]

    above_fast = np.sign(price - ema_f)
    above_slow = np.sign(price - ema_s)
    above_trend = np.sign(price - ema_t)
    fast_over_slow = np.sign(ema_f - ema_s)
    slow_over_trend = np.sign(ema_s - ema_t)

    stacked = (above_fast + above_slow + above_trend + fast_over_slow + slow_over_trend) / 5.0
    return stacked * 100.0


def _momentum_score(df: pd.DataFrame, p: FotsiParams) -> pd.Series:
    k = stochastic_k(df, p.stoch_period, p.stoch_smooth)
    centered = k - 50.0                                # [-50, 50]
    delta = k.diff().fillna(0.0)                       # positive if rising
    direction = np.tanh(delta / 5.0)                   # smooth sign, [-1, 1]
    # Blend the centered level with its direction so an already-high but falling
    # %K is weaker than an already-high and still-rising one.
    score = centered * (0.7 + 0.3 * direction) * 2.0   # scale back to ~[-100, 100]
    return score.clip(-100.0, 100.0)


def _strength_score(df: pd.DataFrame, p: FotsiParams) -> pd.Series:
    signed_adx, _ = adx_directional(df, p.adx_period)
    return signed_adx.clip(-100.0, 100.0)


def fotsi(df: pd.DataFrame, params: FotsiParams | None = None) -> pd.DataFrame:
    """Compute the FOTSI series and its three sub-scores.

    Returns a DataFrame indexed like `df` with columns:
        trend, momentum, strength, fotsi, atr
    """
    p = params or FotsiParams()
    trend = _trend_score(df, p)
    momentum = _momentum_score(df, p)
    strength = _strength_score(df, p)

    total_w = p.weight_trend + p.weight_momentum + p.weight_strength
    if total_w <= 0:
        raise ValueError("FOTSI weights must sum to > 0")

    combined = (
        p.weight_trend * trend
        + p.weight_momentum * momentum
        + p.weight_strength * strength
    ) / total_w

    out = pd.DataFrame(
        {
            "trend": trend,
            "momentum": momentum,
            "strength": strength,
            "fotsi": combined.clip(-100.0, 100.0),
            "atr": atr(df, p.adx_period),
        },
        index=df.index,
    )
    return out


def required_warmup(params: FotsiParams | None = None) -> int:
    p = params or FotsiParams()
    return max(p.ema_trend, p.stoch_period + p.stoch_smooth, p.adx_period * 2) + 5
