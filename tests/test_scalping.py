"""Tests for the ADX+RSI scalping strategy and the trade registry."""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd

from src.indicators.technical import adx, rsi
from src.strategy.base import SignalType
from src.strategy.scalping_adx_rsi import ScalpingAdxRsi
from src.trade_registry import TradeRegistry


def _candles(closes: list[float], highs=None, lows=None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2025-01-01", periods=n, freq="1min", tz="UTC")
    highs = highs if highs is not None else [c + 0.2 for c in closes]
    lows = lows if lows is not None else [c - 0.2 for c in closes]
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


def test_rsi_bounds():
    closes = [100 + i * 0.1 for i in range(60)]
    df = _candles(closes)
    vals = rsi(df, 14).dropna().values
    assert (vals >= 0).all() and (vals <= 100).all()
    # Pure uptrend -> RSI high.
    assert vals[-1] > 70


def test_adx_positive():
    # Strong trend -> ADX should rise.
    closes = [100 + i * 0.5 for i in range(120)]
    df = _candles(closes)
    val = float(adx(df, 14).iloc[-1])
    assert val > 20


def test_scalping_buy_on_oversold_with_trend():
    # Sharp drop -> RSI oversold, ADX strong.
    closes = [100 - i * 0.5 for i in range(120)]
    df = _candles(closes)
    strat = ScalpingAdxRsi(adx_min=15.0)  # relax ADX for the synthetic series
    sig = strat.evaluate(df)
    assert sig.type == SignalType.LONG
    assert sig.stop_price is not None and sig.stop_price < sig.entry_price
    assert sig.target_price is not None and sig.target_price > sig.entry_price
    assert "adx" in sig.details and "rsi" in sig.details


def test_scalping_sell_on_overbought_with_trend():
    closes = [100 + i * 0.5 for i in range(120)]
    df = _candles(closes)
    strat = ScalpingAdxRsi(adx_min=15.0)
    sig = strat.evaluate(df)
    assert sig.type == SignalType.SHORT
    assert sig.stop_price is not None and sig.stop_price > sig.entry_price
    assert sig.target_price is not None and sig.target_price < sig.entry_price


def test_scalping_skips_when_adx_low():
    # Flat price -> ADX ~= 0 even if RSI oscillates.
    closes = [100.0 + (0.05 if i % 2 else -0.05) for i in range(120)]
    df = _candles(closes)
    strat = ScalpingAdxRsi(adx_min=50.0)
    sig = strat.evaluate(df)
    assert sig.type == SignalType.NONE


def test_registry_records_open_and_close(tmp_path: Path):
    async def _run() -> None:
        reg = TradeRegistry(account="demo-test", path=tmp_path / "t.json")
        rec = await reg.record_open(
            trade_id="1",
            instrument="EUR_USD",
            side="LONG",
            units=100,
            entry_price=1.10,
            stop_loss=1.09,
            take_profit=1.11,
            reason="adx_strong+rsi_oversold",
            strategy="scalping_adx_rsi",
            client_tag="demo-test:scalping_adx_rsi:1",
            indicators={"adx": 28.4, "rsi": 32.1},
        )
        assert rec["status"] == "open"
        opens = await reg.list_open()
        assert len(opens) == 1 and opens[0]["id"] == "1"

        closed = await reg.mark_closed("1", sell_price=1.109, close_reason="tp_hit")
        assert closed is not None and closed["status"] == "closed"
        assert closed["sell_price"] == 1.109
        assert await reg.list_open() == []

    asyncio.run(_run())
