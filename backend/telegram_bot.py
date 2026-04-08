from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from telegram import Bot
    from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut

    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed — Telegram disabled")


class TelegramBot:
    def __init__(self, token: str, chat_id: str, min_interval_seconds: float = 10.0) -> None:
        self._chat_id = str(chat_id)
        self._disabled = False
        self._bot: Optional[Bot] = None
        self._min_interval_seconds = max(float(min_interval_seconds), 0.0)
        self._last_sent_at = 0.0
        self._start_time = time.time()

        if not _TELEGRAM_AVAILABLE:
            self._disabled = True
            return

        try:
            self._bot = Bot(token=token)
        except Exception as exc:
            logger.error("TelegramBot: failed to instantiate Bot: %s", exc)
            self._disabled = True

    async def _wait_for_slot(self) -> None:
        if self._min_interval_seconds <= 0:
            return

        remaining = self._min_interval_seconds - (time.monotonic() - self._last_sent_at)
        if remaining > 0:
            await asyncio.sleep(remaining)

    @dataclass
    class SendResult:
        ok: bool
        transient: bool = False
        error: Optional[str] = None

    async def _send(self, text: str, disable_notification: bool = False) -> "TelegramBot.SendResult":
        if self._disabled or self._bot is None:
            return TelegramBot.SendResult(ok=False, transient=False, error="telegram disabled")

        max_retries = 3
        backoff = 5.0

        for attempt in range(1, max_retries + 1):
            try:
                await self._wait_for_slot()
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode="HTML",
                    disable_notification=disable_notification,
                )
                self._last_sent_at = time.monotonic()
                return TelegramBot.SendResult(ok=True)
            except RetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
            except (TimedOut, NetworkError) as exc:
                logger.warning(
                    "TelegramBot: transient error (attempt %d/%d): %s",
                    attempt,
                    max_retries,
                    exc,
                )
                await asyncio.sleep(backoff)
            except TelegramError as exc:
                logger.error("TelegramBot: non-transient error: %s — disabling Telegram", exc)
                self._disabled = True
                return TelegramBot.SendResult(ok=False, transient=False, error=str(exc))
            except Exception as exc:
                logger.error("TelegramBot: unexpected error: %s", exc, exc_info=True)
                return TelegramBot.SendResult(ok=False, transient=True, error=str(exc))

        logger.error("TelegramBot: max retries exceeded, message dropped")
        return TelegramBot.SendResult(ok=False, transient=True, error="max retries exceeded")

    async def send_alert(self, message: str, priority: str) -> None:
        result = await self._send(message, disable_notification=(priority == "LOW"))
        if result.ok:
            logger.info("TelegramBot: alert sent [%s]", priority)

    async def send_startup_message(self, assets: list[str], signals: list[str]) -> None:
        asset_str = ", ".join(assets)
        signal_str = "\n".join(f"  • {signal}" for signal in signals)
        msg = (
            "🚀 <b>hl-signal-daemon online</b>\n\n"
            f"<b>Assets:</b> {asset_str}\n"
            f"<b>Signals:</b>\n{signal_str}"
        )
        await self._send(msg)

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
        runtime_snapshot: Optional[dict] = None,
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
        for key, value in sorted(data_counts.items()):
            lines.append(f"  {key}: {value}")
        if runtime_snapshot:
            lines.extend(
                [
                    "",
                    f"<b>Runtime:</b> {runtime_snapshot.get('status', 'unknown')}",
                    f"Pending refresh assets: {runtime_snapshot.get('refresh', {}).get('pending_count', 0)}",
                    f"Telegram queue depth: {runtime_snapshot.get('telegram_queue_depth', 0)}",
                ]
            )
        await self._send("\n".join(lines), disable_notification=True)
