"""Smoke tests for the strategies, indicators, and risk manager."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.indicators.technical import fib_extensions, fib_retracements, latest_swing
from src.risk.manager import compute_position_units
from src.strategy.base import SignalType
from src.strategy.fibonacci_day_trading import FibonacciDayTrading
from src.strategy.kevin_davey import KevinDaveyBreakout


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


# ---------------------------------------------------------------------------
# Fibonacci helpers
# ---------------------------------------------------------------------------


def test_fib_retracement_levels_match_textbook():
    levels = fib_retracements(swing_low=100.0, swing_high=110.0)
    # 38.2 % retracement from a 100 -> 110 swing sits at 110 - 0.382 * 10
    assert levels["fib_382"] == pytest_approx(106.18)
    assert levels["fib_500"] == pytest_approx(105.0)
    assert levels["fib_618"] == pytest_approx(103.82)
    assert levels["fib_786"] == pytest_approx(102.14)


def test_fib_extension_projects_past_swing():
    ext = fib_extensions(swing_low=100.0, swing_high=110.0)
    # 1.272 extension from a 100 -> 110 swing = 100 + 1.272 * 10
    assert ext["ext_1272"] == pytest_approx(112.72)
    assert ext["ext_1618"] == pytest_approx(116.18)


def test_latest_swing_uptrend_picks_low_then_high():
    # synthetic up-leg: dip then rally
    closes = list(np.linspace(100.0, 95.0, 10)) + list(np.linspace(95.0, 110.0, 20))
    df = _make_df(closes)
    swing = latest_swing(df, lookback=30, uptrend=True)
    assert swing is not None
    swing_low, swing_high, low_idx, high_idx = swing
    assert swing_low < swing_high
    assert low_idx < high_idx


# ---------------------------------------------------------------------------
# Fibonacci day-trading strategy
# ---------------------------------------------------------------------------


def test_fib_long_entry_in_golden_zone():
    # Construct: 30-bar EMA primer, then a clean 100 -> 110 up-leg, then a
    # pullback into the 50 % retracement (~105) with a bullish confirmation
    # candle on the last bar.
    primer = [99.0] * 60
    leg_up = list(np.linspace(99.0, 110.0, 20))
    pullback = [108.0, 107.0, 106.0, 105.0]
    closes = primer + leg_up + pullback
    # Make the last bar bullish: close above its open
    df = _make_df(closes)
    df.loc[df.index[-1], "open"] = closes[-1] - 0.3
    df.loc[df.index[-1], "high"] = closes[-1] + 0.4
    df.loc[df.index[-1], "low"] = closes[-1] - 0.5

    strat = FibonacciDayTrading(
        swing_lookback=30,
        trend_ema_period=20,
        min_atr_pct=0.0,
        trading_window_utc={"start": "00:00", "end": "23:59"},
    )
    sig = strat.evaluate(df)
    assert sig.type == SignalType.LONG
    assert sig.stop_price is not None and sig.stop_price < sig.entry_price
    assert sig.target_price is not None and sig.target_price > sig.entry_price
    # Stop should sit just past the 78.6 % retracement of the 99 -> 110 leg
    assert sig.stop_price < 102.0
    # Details must include the actual numbers being evaluated (per spec).
    for key in ("fib_382", "fib_500", "fib_618", "fib_786", "ext_1272", "swing_low", "swing_high"):
        assert key in sig.details


def test_fib_no_entry_outside_zone():
    # Same up-leg, but price is back near the swing high -> outside zone
    primer = [99.0] * 60
    leg_up = list(np.linspace(99.0, 110.0, 20))
    closes = primer + leg_up + [109.5]
    df = _make_df(closes)
    df.loc[df.index[-1], "open"] = 109.0  # bullish bar but irrelevant

    strat = FibonacciDayTrading(
        swing_lookback=30,
        trend_ema_period=20,
        min_atr_pct=0.0,
        trading_window_utc={"start": "00:00", "end": "23:59"},
    )
    sig = strat.evaluate(df)
    assert sig.type == SignalType.NONE
    assert sig.reason in ("not_in_fib_zone", "awaiting_bull_confirmation")


def test_fib_blocks_outside_trading_window():
    primer = [99.0] * 60
    leg_up = list(np.linspace(99.0, 110.0, 20))
    closes = primer + leg_up + [105.0]
    df = _make_df(closes)
    strat = FibonacciDayTrading(
        swing_lookback=30,
        trend_ema_period=20,
        min_atr_pct=0.0,
        trading_window_utc={"start": "23:50", "end": "23:59"},
    )
    sig = strat.evaluate(df)
    assert sig.type == SignalType.NONE
    assert sig.reason == "outside_trading_window"


def pytest_approx(v: float, rel: float = 1e-3):
    # Tiny shim so we don't add a pytest dep just for `approx`.
    class _Approx:
        def __eq__(self, other):
            return abs(other - v) <= max(abs(v), 1.0) * rel

    return _Approx()
