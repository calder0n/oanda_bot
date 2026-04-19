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
    ) -> None:
        if not self.should_notify("order_filled"):
            return
        risk_amount = equity * (risk_pct / 100.0) if equity > 0 else 0.0
        emoji = "🟢" if side == "LONG" else "🔴"
        text = (
            f"{emoji} *New {side}* on `{instrument}`\n"
            f"• Account: `{account}`\n"
            f"• Units: `{units}`\n"
            f"• Entry: `{entry}`\n"
            f"• Stop: `{stop}`\n"
            f"• Target: `{target}`\n"
            f"• Reason: `{reason}`\n"
            f"• Equity: `{equity:.2f}` (risk ≈ `{risk_amount:.2f}`)"
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
