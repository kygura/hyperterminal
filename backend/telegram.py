"""
Telegram bot wrapper using python-telegram-bot (async).

Sends alerts, startup/shutdown confirmations, and periodic health checks.
Retries transient failures (timeouts, rate limits) up to 3×.
Non-transient errors (bad token) disable Telegram — daemon keeps running.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_TRANSIENT_CODES = {429}  # rate limit; also retry on TimeoutError

try:
    from telegram import Bot
    from telegram.error import TelegramError, NetworkError, TimedOut, RetryAfter
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed — Telegram disabled")


class TelegramBot:
    """Sends messages to a Telegram chat. Gracefully degrades on errors."""

    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = str(chat_id)
        self._bot: Optional[object] = None
        self._disabled = False
        self._start_time = time.time()

        if not _TELEGRAM_AVAILABLE:
            self._disabled = True
            return
        try:
            self._bot = Bot(token=token)
        except Exception as exc:
            logger.error("TelegramBot: failed to instantiate Bot: %s", exc)
            self._disabled = True

    # ------------------------------------------------------------------
    # Internal send with retry
    # ------------------------------------------------------------------

    async def _send(self, text: str, disable_notification: bool = False) -> bool:
        """
        Send a message, retry transient failures up to 3×.
        Returns True on success, False on non-transient error.
        """
        if self._disabled or self._bot is None:
            return False

        max_retries = 3
        backoff = 5.0

        for attempt in range(1, max_retries + 1):
            try:
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode="HTML",
                    disable_notification=disable_notification,
                )
                return True
            except RetryAfter as exc:
                wait = exc.retry_after + 1
                logger.warning(
                    "TelegramBot: rate limited, retry in %ds (attempt %d/%d)",
                    wait, attempt, max_retries,
                )
                await asyncio.sleep(wait)
            except (TimedOut, NetworkError) as exc:
                logger.warning(
                    "TelegramBot: transient error (attempt %d/%d): %s",
                    attempt, max_retries, exc,
                )
                await asyncio.sleep(backoff)
            except TelegramError as exc:
                logger.error("TelegramBot: non-transient error: %s — disabling Telegram", exc)
                self._disabled = True
                return False
            except Exception as exc:
                logger.error("TelegramBot: unexpected error: %s", exc, exc_info=True)
                self._disabled = True
                return False

        logger.error("TelegramBot: max retries exceeded, message dropped")
        return False

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def send_alert(self, message: str, priority: str) -> None:
        disable = priority == "LOW"
        success = await self._send(message, disable_notification=disable)
        if success:
            logger.info("TelegramBot: alert sent [%s]", priority)

    async def send_startup_message(self, assets: list[str], signals: list[str]) -> None:
        asset_str = ", ".join(assets)
        signal_str = "\n".join(f"  • {s}" for s in signals)
        msg = (
            "🚀 <b>hl-signal-daemon online</b>\n\n"
            f"<b>Assets:</b> {asset_str}\n"
            f"<b>Signals:</b>\n{signal_str}"
        )
        await self._send(msg, disable_notification=False)

    async def send_shutdown_message(self) -> None:
        uptime_s = int(time.time() - self._start_time)
        h, rem = divmod(uptime_s, 3600)
        m, s = divmod(rem, 60)
        msg = (
            "🛑 <b>hl-signal-daemon shutting down</b>\n"
            f"Uptime: {h:02d}:{m:02d}:{s:02d}"
        )
        await self._send(msg, disable_notification=True)

    async def send_health_check(
        self,
        uptime_s: int,
        total_alerts: int,
        data_counts: dict,
    ) -> None:
        h, rem = divmod(uptime_s, 3600)
        m, s = divmod(rem, 60)
        lines = [
            "💓 <b>Health Check</b>",
            f"Uptime: {h:02d}:{m:02d}:{s:02d}",
            f"Total alerts sent: {total_alerts}",
            "",
            "<b>Data counts:</b>",
        ]
        funding_counts = data_counts.get("funding", {})
        snap_counts = data_counts.get("snapshots", {})
        trade_counts = data_counts.get("trades", {})
        for coin in sorted(set(list(funding_counts) + list(snap_counts) + list(trade_counts))):
            lines.append(
                f"  {coin}: funding={funding_counts.get(coin, 0)} "
                f"snaps={snap_counts.get(coin, 0)} "
                f"trades={trade_counts.get(coin, 0)}"
            )
        liq = data_counts.get("liquidations", 0)
        lines.append(f"  Liquidations: {liq}")
        await self._send("\n".join(lines), disable_notification=True)

    async def send_ws_warning(self, ws_name: str, failures: int) -> None:
        msg = (
            f"⚠️ <b>WebSocket Warning</b>\n"
            f"Channel <code>{ws_name}</code> has had {failures} consecutive failures."
        )
        await self._send(msg, disable_notification=False)
