"""One worker per OANDA account. Each worker is fully isolated.

Adding another account is a one-liner in accounts.yaml: same strategy (or a
different one) is spawned as another asyncio task against that account's
credentials. Every trade the worker opens is persisted to a shared SQLite
registry so a companion "closer" bot can read the same DB and manage exits.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from .config import AccountConfig
from .notifications import TelegramNotifier
from .oanda_client import OandaClient
from .persistence import TradeRecord, TradeRegistry
from .risk.manager import DailyLossTracker, compute_position_units
from .strategy import Signal, SignalType, build_strategy
from .utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class _TradeMeta:
    trade_id: str
    instrument: str
    opened_at: datetime
    entry_price: float
    stop_price: float | None
    target_price: float | None
    side: str
    bars_held: int = 0


@dataclass
class AccountWorker:
    cfg: AccountConfig
    registry: TradeRegistry | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        client = OandaClient(
            access_token=self.cfg.access_token,
            account_id=self.cfg.account_id,
            environment=self.cfg.environment,
        )
        strategy = build_strategy(
            self.cfg.strategy.name, self.cfg.strategy.params.model_dump()
        )
        loss_tracker = DailyLossTracker(
            max_daily_loss_pct=self.cfg.strategy.params.max_daily_loss_pct
        )
        tg = self.cfg.notifications.telegram
        notifier = TelegramNotifier(
            bot_token=tg.bot_token,
            chat_id=tg.chat_id,
            enabled=tg.enabled,
            notify_on=tg.notify_on,
        )

        instruments = await self._resolve_instruments(client)
        instrument_meta = {i["name"]: i for i in await client.tradeable_instruments()}
        tracked: dict[str, _TradeMeta] = {}

        logger = log.bind(account=self.cfg.name, env=self.cfg.environment)
        logger.info(
            "worker_started",
            instruments=len(instruments),
            strategy=self.cfg.strategy.name,
            granularity=self.cfg.granularity,
            initial_capital_usd=self.cfg.initial_capital_usd,
            risk_per_trade_pct=self.cfg.strategy.params.risk_per_trade_pct,
            telegram_enabled=notifier.enabled,
            registry_path=str(self.registry.path) if self.registry else None,
        )

        try:
            while not self._stop.is_set():
                try:
                    await self._tick(
                        client,
                        strategy,
                        loss_tracker,
                        instruments,
                        instrument_meta,
                        tracked,
                        notifier,
                        logger,
                    )
                except Exception:
                    logger.exception("tick_failed")

                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.cfg.tick_interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await notifier.close()

        logger.info("worker_stopped")

    async def _resolve_instruments(self, client: OandaClient) -> list[str]:
        if isinstance(self.cfg.instruments, list):
            base = list(self.cfg.instruments)
        else:
            base = [i["name"] for i in await client.tradeable_instruments()]
        excluded = set(self.cfg.excluded_instruments or [])
        return [i for i in base if i not in excluded]

    async def _tick(
        self,
        client: OandaClient,
        strategy,
        loss_tracker: DailyLossTracker,
        instruments: list[str],
        instrument_meta: dict[str, dict],
        tracked: dict[str, _TradeMeta],
        notifier: TelegramNotifier,
        logger: structlog.stdlib.BoundLogger,
    ) -> None:
        summary = await client.account_summary()
        equity = float(summary.get("NAV", summary.get("balance", 0)))
        currency = summary.get("currency", "USD")
        loss_tracker.update(equity)

        open_trades = await client.open_trades()
        open_by_instrument = {t["instrument"]: t for t in open_trades}
        open_ids = {str(t.get("id")) for t in open_trades}

        # Reconcile: if a trade we opened is no longer open on OANDA
        # (hit TP/SL or was closed externally), record and notify.
        for inst in list(tracked.keys()):
            meta = tracked[inst]
            if meta.trade_id not in open_ids:
                await self._reconcile_close(
                    client, notifier, logger, meta, reason="tp_sl_or_external"
                )
                tracked.pop(inst, None)

        logger.info(
            "tick",
            equity=round(equity, 2),
            currency=currency,
            open_positions=len(open_by_instrument),
            max_open=self.cfg.strategy.params.max_open_positions,
            daily_blocked=loss_tracker.trading_blocked,
        )

        await self._apply_time_exits(client, notifier, tracked, open_by_instrument, logger)

        if loss_tracker.trading_blocked:
            logger.warning("daily_loss_limit_hit", equity=equity)
            return

        max_open = self.cfg.strategy.params.max_open_positions
        if len(open_by_instrument) >= max_open:
            return

        required = strategy.required_bars()
        risk_pct = self.cfg.strategy.params.risk_per_trade_pct

        for instrument in instruments:
            if self._stop.is_set():
                break
            if instrument in open_by_instrument:
                continue
            if len(open_by_instrument) >= max_open:
                break

            try:
                df = await client.candles(
                    instrument, granularity=self.cfg.granularity, count=required + 20
                )
            except Exception:
                logger.exception("candles_failed", instrument=instrument)
                continue

            if df.empty:
                continue

            signal: Signal = strategy.evaluate(df)

            if self.cfg.log_evaluations:
                logger.info(
                    "evaluation",
                    instrument=instrument,
                    signal=signal.type.value,
                    reason=signal.reason,
                    **signal.details,
                )

            if signal.type in (SignalType.NONE, SignalType.CLOSE):
                continue

            meta = instrument_meta.get(instrument)
            if not meta:
                continue

            units = compute_position_units(
                equity=equity,
                risk_pct=risk_pct,
                entry_price=signal.entry_price or 0.0,
                stop_price=signal.stop_price or 0.0,
                instrument=meta,
                account_currency=currency,
            )
            if units == 0:
                logger.info(
                    "skipped_min_size",
                    instrument=instrument,
                    reason="units_below_minimum",
                    risk_amount=round(equity * risk_pct / 100.0, 2),
                    stop_distance=abs(
                        (signal.entry_price or 0.0) - (signal.stop_price or 0.0)
                    ),
                )
                continue
            if signal.type == SignalType.SHORT and units > 0:
                units = -units
            if signal.type == SignalType.LONG and units < 0:
                units = -units

            try:
                resp = await client.market_order(
                    instrument=instrument,
                    units=units,
                    stop_loss_price=signal.stop_price,
                    take_profit_price=signal.target_price,
                    client_tag=f"{self.cfg.name}:{strategy.name}",
                )
                fill = resp.get("orderFillTransaction") or {}
                trade_opened = fill.get("tradeOpened") or {}
                trade_id = str(trade_opened.get("tradeID") or "")
                fill_price = _safe_float(fill.get("price")) or signal.entry_price
                opened_at_dt = datetime.now(timezone.utc)

                if trade_id:
                    tracked[instrument] = _TradeMeta(
                        trade_id=trade_id,
                        instrument=instrument,
                        opened_at=opened_at_dt,
                        entry_price=float(fill_price or 0.0),
                        stop_price=signal.stop_price,
                        target_price=signal.target_price,
                        side=signal.type.value,
                    )
                    open_by_instrument[instrument] = {
                        "instrument": instrument,
                        "id": trade_id,
                    }
                    if self.registry is not None:
                        self.registry.record_open(
                            TradeRecord(
                                trade_id=trade_id,
                                account_name=self.cfg.name,
                                account_id=self.cfg.account_id,
                                instrument=instrument,
                                side=signal.type.value,
                                units=int(units),
                                entry_price=float(fill_price or 0.0),
                                stop_loss_price=signal.stop_price,
                                take_profit_price=signal.target_price,
                                strategy=strategy.name,
                                reason=signal.reason,
                                opened_at=opened_at_dt,
                                extra={
                                    "equity_at_entry": round(equity, 4),
                                    "risk_pct": risk_pct,
                                    "signal_details": signal.details,
                                },
                            )
                        )
                logger.info(
                    "order_filled",
                    instrument=instrument,
                    units=units,
                    entry=signal.entry_price,
                    fill_price=fill_price,
                    stop=signal.stop_price,
                    target=signal.target_price,
                    reason=signal.reason,
                    trade_id=trade_id,
                )
                await notifier.notify_order_filled(
                    account=self.cfg.name,
                    instrument=instrument,
                    side=signal.type.value,
                    units=units,
                    entry=fill_price,
                    stop=signal.stop_price,
                    target=signal.target_price,
                    reason=signal.reason,
                    equity=equity,
                    risk_pct=risk_pct,
                    trade_id=trade_id or None,
                    strategy=strategy.name,
                    opened_at=opened_at_dt.isoformat(),
                    indicators=_indicator_summary(signal.details),
                )
            except Exception:
                logger.exception("order_failed", instrument=instrument, units=units)

    async def _apply_time_exits(
        self,
        client: OandaClient,
        notifier: TelegramNotifier,
        tracked: dict[str, _TradeMeta],
        open_by_instrument: dict[str, dict],
        logger: structlog.stdlib.BoundLogger,
    ) -> None:
        time_exit = getattr(self.cfg.strategy.params, "time_exit_bars", 0) or 0
        if time_exit <= 0:
            return
        bar_seconds = _granularity_seconds(self.cfg.granularity)
        now = datetime.now(timezone.utc)
        for instrument, meta in list(tracked.items()):
            held_bars = int((now - meta.opened_at).total_seconds() // max(bar_seconds, 1))
            if held_bars >= time_exit:
                try:
                    await client.close_trade(meta.trade_id)
                    logger.info(
                        "time_exit",
                        instrument=instrument,
                        bars_held=held_bars,
                        trade_id=meta.trade_id,
                    )
                    if self.registry is not None:
                        self.registry.mark_closed(
                            trade_id=meta.trade_id,
                            exit_price=None,
                            realized_pl=None,
                            close_reason="time_exit",
                        )
                    await notifier.notify_trade_closed(
                        account=self.cfg.name,
                        instrument=instrument,
                        trade_id=meta.trade_id,
                        exit_price=None,
                        realized_pl=None,
                        close_reason="time_exit",
                    )
                    tracked.pop(instrument, None)
                    open_by_instrument.pop(instrument, None)
                except Exception:
                    logger.exception("time_exit_failed", instrument=instrument)

    async def _reconcile_close(
        self,
        client: OandaClient,
        notifier: TelegramNotifier,
        logger: structlog.stdlib.BoundLogger,
        meta: _TradeMeta,
        reason: str,
    ) -> None:
        exit_price: float | None = None
        realized_pl: float | None = None
        try:
            info = await client.get_trade(meta.trade_id)
            exit_price = _safe_float(info.get("averageClosePrice") or info.get("price"))
            realized_pl = _safe_float(info.get("realizedPL"))
        except Exception:
            logger.warning("close_reconcile_failed", trade_id=meta.trade_id)

        if self.registry is not None:
            self.registry.mark_closed(
                trade_id=meta.trade_id,
                exit_price=exit_price,
                realized_pl=realized_pl,
                close_reason=reason,
            )
        await notifier.notify_trade_closed(
            account=self.cfg.name,
            instrument=meta.instrument,
            trade_id=meta.trade_id,
            exit_price=exit_price,
            realized_pl=realized_pl,
            close_reason=reason,
        )
        logger.info(
            "trade_closed",
            instrument=meta.instrument,
            trade_id=meta.trade_id,
            exit_price=exit_price,
            realized_pl=realized_pl,
            close_reason=reason,
        )


_GRANULARITY_SECONDS = {
    "S5": 5, "S10": 10, "S15": 15, "S30": 30,
    "M1": 60, "M2": 120, "M4": 240, "M5": 300, "M10": 600, "M15": 900,
    "M30": 1800, "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400,
    "H6": 21600, "H8": 28800, "H12": 43200, "D": 86400, "W": 604800,
}


def _granularity_seconds(g: str) -> int:
    return _GRANULARITY_SECONDS.get(g, 60)


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_INDICATOR_KEYS = ("fotsi", "trend", "momentum", "strength", "atr", "atr_pct")


def _indicator_summary(details: dict) -> dict:
    return {k: details[k] for k in _INDICATOR_KEYS if k in details}
