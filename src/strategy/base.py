"""Strategy interface. A strategy turns a candle DataFrame into a Signal."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class SignalType(str, Enum):
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE = "CLOSE"


@dataclass
class Signal:
    type: SignalType
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    reason: str = ""


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def required_bars(self) -> int: ...

    @abstractmethod
    def evaluate(self, df: pd.DataFrame) -> Signal: ...
