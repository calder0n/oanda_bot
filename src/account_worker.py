"""One worker per OANDA account. Each worker is fully isolated.

This makes it trivial to add more accounts: add another block to accounts.yaml,
one more asyncio task is spawned, same strategy (or a different one) runs
against that account's credentials.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import structlog

from .config import AccountConfig
from .notifications import TelegramNotifier
from .oanda_client import OandaClient
from .risk.manager import DailyLossTracker, compute_position_units
from .strategy import Signal, SignalType, build_strategy
from .trade_registry import TradeRegistry
from .utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class _TradeMeta:
    trade_id: str
    instrument: str
    opened_at: datetime
    bars_held: int = 0


@dataclass
class AccountWorker:
    cfg: AccountConfig
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

        state_dir = Path(os.environ.get("STATE_DIR", "/app/state"))
        registry = TradeRegistry(
            account=self.cfg.name,
            path=state_dir / f"trades_{self.cfg.name}.json",
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
                        registry,
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
        registry: TradeRegistry,
        logger: structlog.stdlib.BoundLogger,
    ) -> None:
        summary = await client.account_summary()
        equity = float(summary.get("NAV", summary.get("balance", 0)))
        currency = summary.get("currency", "USD")
        loss_tracker.update(equity)

        open_trades = await client.open_trades()
        open_by_instrument = {t["instrument"]: t for t in open_trades}
        # prune tracked that are no longer open
        for inst in list(tracked.keys()):
            if inst not in open_by_instrument:
                tracked.pop(inst, None)

        logger.info(
            "tick",
            equity=round(equity, 2),
            currency=currency,
            open_positions=len(open_by_instrument),
            max_open=self.cfg.strategy.params.max_open_positions,
            daily_blocked=loss_tracker.trading_blocked,
        )

        # time-based exit
        await self._apply_time_exits(client, strategy, open_by_instrument, tracked, logger)

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

            client_tag = (
                f"{self.cfg.name}:{strategy.name}:"
                f"{int(datetime.now(timezone.utc).timestamp() * 1000)}"
            )
            try:
                resp = await client.market_order(
                    instrument=instrument,
                    units=units,
                    stop_loss_price=signal.stop_price,
                    take_profit_price=signal.target_price,
                    client_tag=client_tag,
                )
                fill = resp.get("orderFillTransaction") or {}
                trade_id = str(fill.get("tradeOpened", {}).get("tradeID", ""))
                fill_price = fill.get("price")
                entry_price = float(fill_price) if fill_price else signal.entry_price
                if trade_id:
                    tracked[instrument] = _TradeMeta(
                        trade_id=trade_id,
                        instrument=instrument,
                        opened_at=datetime.now(timezone.utc),
                    )
                    open_by_instrument[instrument] = {"instrument": instrument, "id": trade_id}
                    await registry.record_open(
                        trade_id=trade_id,
                        instrument=instrument,
                        side=signal.type.value,
                        units=units,
                        entry_price=entry_price,
                        stop_loss=signal.stop_price,
                        take_profit=signal.target_price,
                        reason=signal.reason,
                        strategy=strategy.name,
                        client_tag=client_tag,
                        indicators=signal.details,
                    )
                logger.info(
                    "order_filled",
                    instrument=instrument,
                    units=units,
                    entry=entry_price,
                    stop=signal.stop_price,
                    target=signal.target_price,
                    reason=signal.reason,
                    trade_id=trade_id,
                    client_tag=client_tag,
                )
                await notifier.notify_order_filled(
                    account=self.cfg.name,
                    instrument=instrument,
                    side=signal.type.value,
                    units=units,
                    entry=entry_price,
                    stop=signal.stop_price,
                    target=signal.target_price,
                    reason=signal.reason,
                    equity=equity,
                    risk_pct=risk_pct,
                    trade_id=trade_id,
                )
            except Exception:
                logger.exception("order_failed", instrument=instrument, units=units)

    async def _apply_time_exits(
        self,
        client: OandaClient,
        strategy,
        open_by_instrument: dict[str, dict],
        tracked: dict[str, _TradeMeta],
        logger: structlog.stdlib.BoundLogger,
    ) -> None:
        time_exit = self.cfg.strategy.params.time_exit_bars
        if time_exit <= 0:
            return
        bar_seconds = _granularity_seconds(self.cfg.granularity)
        now = datetime.now(timezone.utc)
        for instrument, trade in list(open_by_instrument.items()):
            meta = tracked.get(instrument)
            if not meta:
                continue
            held_bars = int((now - meta.opened_at).total_seconds() // max(bar_seconds, 1))
            if held_bars >= time_exit:
                try:
                    await client.close_trade(meta.trade_id)
                    logger.info("time_exit", instrument=instrument, bars_held=held_bars)
                    tracked.pop(instrument, None)
                    open_by_instrument.pop(instrument, None)
                except Exception:
                    logger.exception("time_exit_failed", instrument=instrument)


_GRANULARITY_SECONDS = {
    "S5": 5, "S10": 10, "S15": 15, "S30": 30,
    "M1": 60, "M2": 120, "M4": 240, "M5": 300, "M10": 600, "M15": 900,
    "M30": 1800, "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400,
    "H6": 21600, "H8": 28800, "H12": 43200, "D": 86400, "W": 604800,
}


def _granularity_seconds(g: str) -> int:
    return _GRANULARITY_SECONDS.get(g, 900)
