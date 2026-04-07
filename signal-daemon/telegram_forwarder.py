#!/usr/bin/env python3
"""Tail the signal JSONL file and forward formatted signals to Telegram."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_SIGNAL_FILE = Path(__file__).resolve().parents[1] / "data" / "signals.jsonl"
DEFAULT_POLL_INTERVAL = 1.0
RATE_LIMIT_SECONDS = 10.0
DISABLED_SLEEP_SECONDS = 60.0
HTTP_TIMEOUT_SECONDS = 10.0
TRUE_VALUES = {"1", "true", "yes", "on"}


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def env_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_VALUES


def format_compact_number(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return str(value)
    abs_value = abs(float(value))
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:.2f}" if isinstance(value, float) and not value.is_integer() else f"{int(value)}"


def escape_markdown(text: Any) -> str:
    escaped = str(text)
    for char in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def format_signal_message(signal: dict[str, Any]) -> str:
    meta = signal.get("meta") or {}
    direction = str(signal.get("direction", "Unknown")).upper()
    direction_icon = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪️"
    strength = signal.get("strength", 0)
    try:
        strength_text = f"{float(strength):.2f}"
    except (TypeError, ValueError):
        strength_text = str(strength)

    lines = [
        f"{direction_icon} *{escape_markdown(signal.get('asset', 'UNKNOWN'))} {escape_markdown(direction)}*",
        f"*Type:* {escape_markdown(signal.get('type', 'unknown'))}",
        f"*Strength:* `{escape_markdown(strength_text)}`  *Timeframe:* `{escape_markdown(meta.get('timeframe', 'n/a'))}`",
        f"*Ratio:* `{escape_markdown(meta.get('ratio', 'n/a'))}`",
        (
            "*Bid/Ask:* `"
            f"{escape_markdown(format_compact_number(meta.get('bid_volume', 0)))} / "
            f"{escape_markdown(format_compact_number(meta.get('ask_volume', 0)))}"
            "`"
        ),
        f"*Timestamp:* `{escape_markdown(signal.get('timestamp', 'n/a'))}`",
        f"*Signal ID:* `{escape_markdown(signal.get('id', 'unknown'))}`",
    ]
    return "\n".join(lines)


class JsonlTailer:
    def __init__(self, file_path: Path, poll_interval: float = DEFAULT_POLL_INTERVAL) -> None:
        self.file_path = file_path
        self.poll_interval = poll_interval
        self.handle = None
        self.position = 0

    def _open_at_end(self) -> None:
        self.handle = self.file_path.open("r", encoding="utf-8")
        self.handle.seek(0, os.SEEK_END)
        self.position = self.handle.tell()
        log(f"Tailing {self.file_path} from byte {self.position}")

    def poll(self) -> dict[str, Any] | None:
        while True:
            if self.handle is None:
                if not self.file_path.exists():
                    log(f"Waiting for signal file: {self.file_path}")
                    time.sleep(self.poll_interval)
                    return None
                self._open_at_end()

            assert self.handle is not None

            line = self.handle.readline()
            if line:
                self.position = self.handle.tell()
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    log(f"Skipping malformed JSONL line: {exc}")
                    continue
                if not isinstance(payload, dict):
                    log("Skipping non-object signal payload")
                    continue
                return payload

            if not self.file_path.exists():
                log("Signal file disappeared; reopening when it returns")
                self.handle.close()
                self.handle = None
                time.sleep(self.poll_interval)
                return None

            current_size = self.file_path.stat().st_size
            if current_size < self.position:
                log("Detected signal file truncation; reopening tail")
                self.handle.close()
                self.handle = None
                return None

            time.sleep(self.poll_interval)
            return None


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id

    def send(self, text: str) -> bool:
        payload = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            log(f"Telegram API HTTP error {exc.code}: {error_body}")
            return False
        except urllib.error.URLError as exc:
            log(f"Telegram API connection error: {exc}")
            return False

        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            log("Telegram API returned a non-JSON response")
            return False

        if not result.get("ok"):
            log(f"Telegram API rejected message: {result}")
            return False
        return True


def run_disabled_loop(reason: str) -> int:
    log(reason)
    while True:
        time.sleep(DISABLED_SLEEP_SECONDS)


def main() -> int:
    if not env_enabled(os.getenv("TELEGRAM_ENABLED")):
        return run_disabled_loop("Telegram forwarding disabled (set TELEGRAM_ENABLED=true to enable)")

    bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not bot_token or not chat_id:
        return run_disabled_loop(
            "Telegram forwarding misconfigured (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required)"
        )

    signal_file = Path(os.getenv("SIGNAL_JSONL_PATH", str(DEFAULT_SIGNAL_FILE))).expanduser().resolve()
    tailer = JsonlTailer(signal_file)
    telegram = TelegramClient(bot_token=bot_token, chat_id=chat_id)
    pending_signal: dict[str, Any] | None = None
    last_attempt_at = 0.0

    log("Telegram forwarding enabled")
    log(f"Using signal file: {signal_file}")

    while True:
        signal = tailer.poll()
        if signal is not None:
            now = time.monotonic()
            if now - last_attempt_at >= RATE_LIMIT_SECONDS and pending_signal is None:
                message = format_signal_message(signal)
                sent = telegram.send(message)
                last_attempt_at = time.monotonic()
                status = "sent" if sent else "failed"
                log(f"Telegram delivery {status} for signal {signal.get('id', 'unknown')}")
            else:
                pending_signal = signal
                log(f"Queued latest signal {signal.get('id', 'unknown')} during rate limit window")

        if pending_signal is not None and time.monotonic() - last_attempt_at >= RATE_LIMIT_SECONDS:
            message = format_signal_message(pending_signal)
            sent = telegram.send(message)
            last_attempt_at = time.monotonic()
            status = "sent" if sent else "failed"
            log(f"Telegram delivery {status} for queued signal {pending_signal.get('id', 'unknown')}")
            pending_signal = None


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("Telegram forwarder stopped")
        sys.exit(0)
