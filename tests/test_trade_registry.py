"""Smoke tests for the SQLite trade registry."""
from __future__ import annotations

from datetime import datetime, timezone

from src.persistence import TradeRecord, TradeRegistry


def test_registry_roundtrip(tmp_path):
    reg = TradeRegistry(tmp_path / "trades.sqlite3")
    rec = TradeRecord(
        trade_id="42",
        account_name="demo-primary",
        account_id="101-001-0000000-001",
        instrument="EUR_USD",
        side="LONG",
        units=10_000,
        entry_price=1.1000,
        stop_loss_price=1.0985,
        take_profit_price=1.1030,
        strategy="magala_fotsi",
        reason="fotsi_long",
        opened_at=datetime.now(timezone.utc),
        extra={"fotsi": 62.5},
    )
    reg.record_open(rec)

    opens = reg.open_trades()
    assert len(opens) == 1
    assert opens[0]["trade_id"] == "42"
    assert opens[0]["take_profit_price"] == 1.1030
    assert opens[0]["stop_loss_price"] == 1.0985

    reg.mark_closed(
        trade_id="42",
        exit_price=1.1029,
        realized_pl=28.5,
        close_reason="tp",
    )

    assert reg.open_trades() == []
    row = reg.get("42")
    assert row is not None
    assert row["close_reason"] == "tp"
    assert row["exit_price"] == 1.1029
    assert row["realized_pl"] == 28.5
