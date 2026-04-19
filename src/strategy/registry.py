"""Simple name->class registry so new strategies can be dropped in."""
from __future__ import annotations

from .base import Strategy
from .kevin_davey import KevinDaveyBreakout
from .scalping_adx_rsi import ScalpingAdxRsi

_REGISTRY: dict[str, type[Strategy]] = {
    KevinDaveyBreakout.name: KevinDaveyBreakout,
    ScalpingAdxRsi.name: ScalpingAdxRsi,
}


def build_strategy(name: str, params: dict) -> Strategy:
    if name not in _REGISTRY:
        raise ValueError(f"Unknown strategy '{name}'. Available: {list(_REGISTRY)}")
    return _REGISTRY[name](**params)
