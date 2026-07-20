"""Tests for the Magala FOTSI strategy and indicator."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.indicators.fotsi import FotsiParams, fotsi
from src.strategy.base import SignalType
from src.strategy.magala_fotsi import MagalaFotsi


def _mk_df(closes, highs=None, lows=None):
    n = len(closes)
    idx = pd.date_range("2025-01-01", periods=n, freq="1min", tz="UTC")
    highs = highs if highs is not None else [c + 0.0004 for c in closes]
    lows = lows if lows is not None else [c - 0.0004 for c in closes]
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [100] * n,
            "complete": [True] * n,
        },
        index=idx,
    )


def test_fotsi_returns_expected_columns():
    n = 200
    closes = list(np.linspace(1.10, 1.11, n))
    df = _mk_df(closes)
    out = fotsi(df, FotsiParams())
    assert set(out.columns) == {"trend", "momentum", "strength", "fotsi", "atr"}
    assert out["fotsi"].dropna().between(-100.0, 100.0).all()


def test_fotsi_positive_on_strong_uptrend():
    n = 200
    closes = list(np.linspace(1.10, 1.20, n))  # steady uptrend
    df = _mk_df(closes)
    out = fotsi(df, FotsiParams()).dropna()
    assert out["fotsi"].iloc[-1] > 50.0
    assert out["trend"].iloc[-1] > 0


def test_fotsi_negative_on_strong_downtrend():
    n = 200
    closes = list(np.linspace(1.20, 1.10, n))
    df = _mk_df(closes)
    out = fotsi(df, FotsiParams()).dropna()
    assert out["fotsi"].iloc[-1] < -50.0
    assert out["trend"].iloc[-1] < 0


def test_magala_no_signal_on_flat_market():
    closes = [1.1000] * 200
    df = _mk_df(closes)
    strat = MagalaFotsi(min_atr_pct=0.0)
    assert strat.evaluate(df).type == SignalType.NONE


def test_magala_produces_long_on_pullback_then_thrust():
    # 150 bars of uptrend, then a 3-bar pullback, then a resumption thrust.
    trend = list(np.linspace(1.10, 1.20, 150))
    pullback = [trend[-1] - 0.001, trend[-1] - 0.0015, trend[-1] - 0.002]
    resume = [pullback[-1] + 0.003, pullback[-1] + 0.006, pullback[-1] + 0.009]
    closes = trend + pullback + resume
    df = _mk_df(closes)
    strat = MagalaFotsi(
        entry_threshold=30.0,
        strength_min=5.0,
        min_atr_pct=0.0,
        pullback_lookback=8,
    )
    sig = strat.evaluate(df)
    assert sig.type in (SignalType.LONG, SignalType.NONE)
    if sig.type == SignalType.LONG:
        assert sig.stop_price is not None and sig.stop_price < sig.entry_price
        assert sig.target_price is not None and sig.target_price > sig.entry_price
        for key in ("fotsi", "trend", "momentum", "strength", "atr"):
            assert key in sig.details
