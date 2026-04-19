"""Position sizing and daily-loss kill switch - Davey-style risk controls."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


@dataclass
class DailyLossTracker:
    """Tracks realized P&L for the current UTC day to enforce a hard stop."""

    max_daily_loss_pct: float
    _day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    _start_equity: float = 0.0
    _blocked: bool = False

    def reset_if_new_day(self, equity: float) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day = today
            self._start_equity = equity
            self._blocked = False
        elif self._start_equity == 0.0:
            self._start_equity = equity

    def update(self, equity: float) -> None:
        self.reset_if_new_day(equity)
        if self._start_equity <= 0:
            return
        dd_pct = (self._start_equity - equity) / self._start_equity * 100.0
        if dd_pct >= self.max_daily_loss_pct:
            self._blocked = True

    @property
    def trading_blocked(self) -> bool:
        return self._blocked


def compute_position_units(
    equity: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    instrument: dict,
    account_currency: str,
) -> int:
    """Return signed unit count (positive long, negative short) or 0 if invalid.

    Uses a simple conservative approximation: risk_amount / stop_distance.
    Pip/margin conversion is handled by OANDA when the stop is attached to the
    order; we convert to units assuming quote currency == account currency or
    that OANDA's internal conversion keeps the approximation within tolerance.
    For JPY-quoted pairs and metals this still yields a risk bounded by
    risk_pct * equity at worst-case fill; tighten risk_pct to compensate.
    """
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0 or equity <= 0 or risk_pct <= 0:
        return 0

    risk_amount = equity * (risk_pct / 100.0)
    raw_units = risk_amount / stop_distance

    direction = 1 if entry_price > stop_price else -1
    units = int(raw_units) * direction

    # Respect instrument min/max trade size
    min_units = int(float(instrument.get("minimumTradeSize", 1)))
    max_units = int(float(instrument.get("maximumOrderUnits", 10_000_000)))
    if abs(units) < min_units:
        return 0
    if abs(units) > max_units:
        units = max_units * direction
    return units
