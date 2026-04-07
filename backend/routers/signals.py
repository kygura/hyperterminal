from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import suppress
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from models import Signal, parse_signal_json

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SIGNALS_PATH = PROJECT_ROOT / "data" / "signals.jsonl"


class SignalBroadcaster:
    def __init__(
        self,
        signals_path: Path = DEFAULT_SIGNALS_PATH,
        poll_interval: float = 0.5,
        history_limit: int = 20,
    ) -> None:
        self.signals_path = signals_path
        self.poll_interval = poll_interval
        self.history_limit = history_limit
        self.connections: set[WebSocket] = set()
        self._task: asyncio.Task | None = None
        self._position = 0

    def ensure_file(self) -> None:
        self.signals_path.parent.mkdir(parents=True, exist_ok=True)
        self.signals_path.touch(exist_ok=True)

    async def start(self) -> None:
        self.ensure_file()
        self._position = self.signals_path.stat().st_size

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._tail_loop())

    async def stop(self) -> None:
        if self._task is None:
            return

        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)
        await self._send_recent_history(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def _send_recent_history(self, websocket: WebSocket) -> None:
        for signal in self._read_recent_signals():
            await websocket.send_json(signal.to_jsonable())

    def _read_recent_signals(self) -> list[Signal]:
        self.ensure_file()
        recent_lines: deque[str] = deque(maxlen=self.history_limit)

        with self.signals_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    recent_lines.append(line)

        parsed: list[Signal] = []
        for line in recent_lines:
            try:
                parsed.append(parse_signal_json(line))
            except Exception as exc:
                logger.warning("Skipping invalid signal line in history: %s", exc)
        return parsed

    def _read_new_signals(self) -> list[Signal]:
        self.ensure_file()
        current_size = self.signals_path.stat().st_size

        if self._position > current_size:
            self._position = 0

        signals: list[Signal] = []
        with self.signals_path.open("r", encoding="utf-8") as handle:
            handle.seek(self._position)

            while True:
                raw_line = handle.readline()
                if not raw_line:
                    break

                self._position = handle.tell()
                raw_line = raw_line.strip()
                if not raw_line:
                    continue

                try:
                    signals.append(parse_signal_json(raw_line))
                except Exception as exc:
                    logger.warning("Skipping invalid signal line: %s", exc)

        return signals

    async def broadcast(self, signal: Signal) -> None:
        stale: list[WebSocket] = []

        for websocket in list(self.connections):
            try:
                await websocket.send_json(signal.to_jsonable())
            except Exception:
                stale.append(websocket)

        for websocket in stale:
            self.disconnect(websocket)

    async def _tail_loop(self) -> None:
        while True:
            for signal in self._read_new_signals():
                await self.broadcast(signal)
            await asyncio.sleep(self.poll_interval)


signal_broadcaster = SignalBroadcaster()
router = APIRouter()


@router.websocket("/ws/signals")
async def websocket_signals(websocket: WebSocket) -> None:
    await signal_broadcaster.connect(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        signal_broadcaster.disconnect(websocket)
    except Exception:
        signal_broadcaster.disconnect(websocket)
