from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


TaskFactory = Callable[[], Awaitable[None]]


@dataclass
class TaskSpec:
    name: str
    factory: TaskFactory
    critical: bool = False
    restartable: bool = True
    max_backoff_seconds: float = 60.0


@dataclass
class TaskState:
    name: str
    critical: bool
    status: str = "pending"
    restarts: int = 0
    last_started_at: Optional[float] = None
    last_success_at: Optional[float] = None
    last_error: Optional[str] = None
    next_restart_at: Optional[float] = None


class RuntimeSupervisor:
    def __init__(self, *, stop_event: Optional[asyncio.Event] = None) -> None:
        self.stop_event = stop_event or asyncio.Event()
        self._specs: dict[str, TaskSpec] = {}
        self._states: dict[str, TaskState] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._started_at: Optional[float] = None

    def add(self, spec: TaskSpec) -> None:
        self._specs[spec.name] = spec
        self._states[spec.name] = TaskState(name=spec.name, critical=spec.critical)

    async def start(self) -> None:
        if self._started_at is None:
            self._started_at = time.time()
        for name in self._specs:
            if name not in self._tasks or self._tasks[name].done():
                self._tasks[name] = asyncio.create_task(self._run_spec(name), name=f"supervised:{name}")

    async def stop(self) -> None:
        self.stop_event.set()
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def _run_spec(self, name: str) -> None:
        spec = self._specs[name]
        state = self._states[name]
        backoff = 1.0
        while not self.stop_event.is_set():
            state.status = "running"
            state.last_started_at = time.time()
            state.next_restart_at = None
            try:
                await spec.factory()
                state.last_success_at = time.time()
                if self.stop_event.is_set() or not spec.restartable:
                    state.status = "stopped"
                    return
                state.status = "restarting"
                state.last_error = "task exited unexpectedly"
            except asyncio.CancelledError:
                state.status = "cancelled"
                raise
            except Exception as exc:
                state.status = "failed"
                state.last_error = str(exc)
                logger.error("Supervised task %s failed: %s", name, exc, exc_info=True)
                if not spec.restartable or self.stop_event.is_set():
                    return

            state.restarts += 1
            delay = min(backoff, spec.max_backoff_seconds) + random.uniform(0.0, 0.25)
            state.next_restart_at = time.time() + delay
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, spec.max_backoff_seconds)

    def snapshot(self) -> dict:
        tasks: dict[str, dict] = {}
        for name, state in self._states.items():
            tasks[name] = {
                "critical": state.critical,
                "status": state.status,
                "restarts": state.restarts,
                "last_started_at": state.last_started_at,
                "last_success_at": state.last_success_at,
                "last_error": state.last_error,
                "next_restart_at": state.next_restart_at,
            }

        overall = "healthy"
        if any(state.critical and state.status in {"failed", "restarting"} for state in self._states.values()):
            overall = "degraded"
        if any(state.critical and state.status == "pending" for state in self._states.values()) and self._started_at is not None:
            overall = "degraded"
        return {
            "status": overall,
            "started_at": self._started_at,
            "tasks": tasks,
        }
