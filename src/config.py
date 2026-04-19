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
    lookback_bars: int = 40
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    atr_target_mult: float = 4.0
    volatility_filter_atr_pct: float = 0.0003
    time_exit_bars: int = 48
    risk_per_trade_pct: float = 0.5
    max_open_positions: int = 5
    max_daily_loss_pct: float = 2.0
    trading_window_utc: TradingWindow = Field(default_factory=TradingWindow)


class StrategyConfig(BaseModel):
    name: str = "kevin_davey_breakout"
    params: StrategyParams = Field(default_factory=StrategyParams)


class AccountConfig(BaseModel):
    name: str
    account_id: str
    access_token: str
    environment: str = "practice"
    granularity: str = "M15"
    tick_interval_seconds: int = 30
    instruments: Any = "ALL_TRADEABLE"
    excluded_instruments: list[str] = Field(default_factory=list)
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
