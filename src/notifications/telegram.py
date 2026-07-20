"""Async Telegram notifier. Never raises into the trading loop."""
from __future__ import annotations

import asyncio

import aiohttp

from ..utils.logger import get_logger

log = get_logger(__name__)


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
        notify_on: list[str] | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(enabled and bot_token and chat_id)
        self.notify_on = set(notify_on or ["order_filled"])
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def should_notify(self, event: str) -> bool:
        return self.enabled and event in self.notify_on

    async def send(self, text: str) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    log.warning(
                        "telegram_send_failed", status=resp.status, body=body[:500]
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("telegram_exception", error=str(exc))

    async def notify_order_filled(
        self,
        account: str,
        instrument: str,
        side: str,
        units: int,
        entry: float | None,
        stop: float | None,
        target: float | None,
        reason: str,
        equity: float,
        risk_pct: float,
        trade_id: str | None = None,
        strategy: str | None = None,
        opened_at: str | None = None,
        indicators: dict | None = None,
    ) -> None:
        if not self.should_notify("order_filled"):
            return
        risk_amount = equity * (risk_pct / 100.0) if equity > 0 else 0.0
        emoji = "🟢" if side == "LONG" else "🔴"
        lines = [
            f"{emoji} *New {side}* on `{instrument}`",
            f"• Account: `{account}`",
            f"• Trade ID: `{trade_id or 'n/a'}`",
            f"• Strategy: `{strategy or 'n/a'}`",
            f"• Units: `{units}`",
            f"• Entry: `{entry}`",
            f"• Stop-Loss: `{stop}`",
            f"• Take-Profit: `{target}`",
            f"• Reason: `{reason}`",
            f"• Equity: `{equity:.2f}` (risk ≈ `{risk_amount:.2f}`)",
        ]
        if opened_at:
            lines.append(f"• Opened at: `{opened_at}`")
        if indicators:
            parts = ", ".join(f"{k}={v}" for k, v in indicators.items())
            lines.append(f"• Indicators: `{parts}`")
        await self.send("\n".join(lines))

    async def notify_trade_closed(
        self,
        account: str,
        instrument: str,
        trade_id: str,
        exit_price: float | None,
        realized_pl: float | None,
        close_reason: str,
    ) -> None:
        if not self.should_notify("trade_closed"):
            return
        pl = f"{realized_pl:+.2f}" if realized_pl is not None else "n/a"
        text = (
            f"⚪ *Trade closed* on `{instrument}`\n"
            f"• Account: `{account}`\n"
            f"• Trade ID: `{trade_id}`\n"
            f"• Exit: `{exit_price}`\n"
            f"• Reason: `{close_reason}`\n"
            f"• Realized P/L: `{pl}`"
        )
        await self.send(text)

    async def notify_startup(self, account: str, equity: float, instruments: int) -> None:
        if not self.should_notify("startup"):
            return
        await self.send(
            f"🚀 *Bot started* for `{account}`\n"
            f"• Equity: `{equity:.2f}`\n"
            f"• Instruments: `{instruments}`"
        )
