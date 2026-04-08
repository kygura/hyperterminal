from __future__ import annotations

import asyncio

from runtime.signal_runtime import SignalRefreshCoordinator
from runtime.supervisor import RuntimeSupervisor, TaskSpec


def test_runtime_supervisor_restarts_failed_task():
    async def scenario() -> tuple[int, dict]:
        stop_event = asyncio.Event()
        supervisor = RuntimeSupervisor(stop_event=stop_event)
        attempts = {"count": 0}

        async def flaky() -> None:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("boom")
            await stop_event.wait()

        supervisor.add(TaskSpec("flaky", flaky, critical=True))
        await supervisor.start()
        await asyncio.sleep(1.5)
        snapshot = supervisor.snapshot()
        await supervisor.stop()
        return attempts["count"], snapshot

    attempts, snapshot = asyncio.run(scenario())

    assert attempts >= 2
    assert snapshot["tasks"]["flaky"]["restarts"] >= 1


def test_signal_refresh_coordinator_coalesces_updates():
    async def scenario() -> list[str]:
        stop_event = asyncio.Event()
        coordinator = SignalRefreshCoordinator(["BTC", "ETH"], max_pending_assets=4)
        coordinator.mark_dirty("BTC")
        coordinator.mark_dirty("BTC")
        coordinator.mark_dirty("ETH")
        batch = await coordinator.wait_for_batch(stop_event, 0.01)
        return batch

    batch = asyncio.run(scenario())
    assert batch == ["BTC", "ETH"]


def test_signal_refresh_coordinator_overflow_promotes_full_refresh():
    async def scenario() -> tuple[list[str], dict]:
        stop_event = asyncio.Event()
        coordinator = SignalRefreshCoordinator(["BTC", "ETH"], max_pending_assets=1)
        coordinator.mark_dirty("BTC")
        coordinator.mark_dirty("ETH")
        batch = await coordinator.wait_for_batch(stop_event, 0.01)
        return batch, coordinator.snapshot()

    batch, snapshot = asyncio.run(scenario())
    assert batch == ["BTC", "ETH"]
    assert snapshot["overflow_count"] == 1
