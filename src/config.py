"""Typed configuration loaded from YAML and environment variables."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class TradingWindow(BaseModel):
    start: str = "00:00"
    end: str = "23:59"


class StrategyParams(BaseModel):
    # Shared risk-control fields (used by every strategy).
    risk_per_trade_pct: float = 1.0
    max_open_positions: int = 3
    max_daily_loss_pct: float = 3.0
    time_exit_bars: int = 0                 # 0 disables the time exit
    trading_window_utc: TradingWindow = Field(default_factory=TradingWindow)

    # Legacy Kevin-Davey breakout parameters (kept for backwards compat).
    lookback_bars: int = 40
    atr_period: int = 14
    volatility_filter_atr_pct: float = 0.0003

    # Magala FOTSI parameters.
    ema_fast: int = 8
    ema_slow: int = 21
    ema_trend: int = 55
    stoch_period: int = 14
    stoch_smooth: int = 3
    adx_period: int = 14
    weight_trend: float = 0.4
    weight_momentum: float = 0.3
    weight_strength: float = 0.3
    entry_threshold: float = 55.0
    strength_min: float = 20.0
    pullback_lookback: int = 5
    min_atr_pct: float = 0.00015

    # Shared exit multipliers (both strategies use ATR-based stops/targets).
    atr_stop_mult: float = 1.5
    atr_target_mult: float = 3.0


class StrategyConfig(BaseModel):
    name: str = "magala_fotsi"
    params: StrategyParams = Field(default_factory=StrategyParams)


class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    notify_on: list[str] = Field(
        default_factory=lambda: ["order_filled", "trade_closed"]
    )


class NotificationsConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)


class AccountConfig(BaseModel):
    name: str
    account_id: str
    access_token: str
    environment: str = "practice"
    granularity: str = "M1"
    tick_interval_seconds: int = 20
    instruments: Any = "ALL_TRADEABLE"
    excluded_instruments: list[str] = Field(default_factory=list)
    initial_capital_usd: float = 1000.0
    log_evaluations: bool = True
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)

    @field_validator("environment")
    @classmethod
    def _env(cls, v: str) -> str:
        if v not in ("practice", "live"):
            raise ValueError("environment must be 'practice' or 'live'")
        return v


class RootConfig(BaseModel):
    accounts: list[AccountConfig]

    @field_validator("accounts")
    @classmethod
    def _non_empty(cls, v: list[AccountConfig]) -> list[AccountConfig]:
        if not v:
            raise ValueError("at least one account must be configured")
        names = [a.name for a in v]
        if len(set(names)) != len(names):
            raise ValueError("account names must be unique")
        return v


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_config(path: str | os.PathLike[str] | None = None) -> RootConfig:
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", "config/accounts.yaml"))
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")

    raw = yaml.safe_load(cfg_path.read_text()) or {}
    defaults = raw.get("defaults", {}) or {}
    accounts_raw = raw.get("accounts", []) or []

    merged: list[dict] = []
    for acct in accounts_raw:
        merged.append(_deep_merge(defaults, acct))

    return RootConfig(accounts=[AccountConfig(**a) for a in merged])
