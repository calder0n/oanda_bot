"""Smoke tests for the Kevin Davey breakout strategy and risk manager."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.risk.manager import compute_position_units
from src.strategy.kevin_davey import KevinDaveyBreakout
from src.strategy.base import SignalType


def _make_df(closes, highs=None, lows=None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    highs = highs if highs is not None else [c + 0.5 for c in closes]
    lows = lows if lows is not None else [c - 0.5 for c in closes]
    df = pd.DataFrame(
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
    return df


def test_breakout_long():
    base = list(np.linspace(100.0, 101.0, 80))
    closes = base + [105.0]  # breakout
    df = _make_df(closes)
    strat = KevinDaveyBreakout(
        lookback_bars=40,
        atr_period=14,
        volatility_filter_atr_pct=0.0,
        trading_window_utc={"start": "00:00", "end": "23:59"},
    )
    sig = strat.evaluate(df)
    assert sig.type == SignalType.LONG
    assert sig.stop_price is not None and sig.stop_price < sig.entry_price
    assert sig.target_price is not None and sig.target_price > sig.entry_price


def test_no_signal_when_flat():
    closes = [100.0] * 80
    df = _make_df(closes)
    strat = KevinDaveyBreakout(volatility_filter_atr_pct=0.0)
    assert strat.evaluate(df).type == SignalType.NONE


def test_position_sizing_respects_risk():
    units = compute_position_units(
        equity=10_000,
        risk_pct=1.0,
        entry_price=1.1000,
        stop_price=1.0950,
        instrument={"minimumTradeSize": 1, "maximumOrderUnits": 10_000_000},
        account_currency="USD",
    )
    # Risk = $100, stop distance = 0.005 -> ~20,000 units (long)
    assert units > 0
    assert abs(units) <= 20_000


def test_position_sizing_short():
    units = compute_position_units(
        equity=10_000,
        risk_pct=1.0,
        entry_price=1.0950,
        stop_price=1.1000,
        instrument={"minimumTradeSize": 1, "maximumOrderUnits": 10_000_000},
        account_currency="USD",
    )
    assert units < 0
