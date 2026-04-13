from __future__ import annotations

import asyncio

import runtime.signal_runtime as signal_runtime
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


def test_signal_runtime_prefers_bybit_context_hydration(monkeypatch, tmp_path):
    config = {
        "assets": ["BTC", "ETH"],
        "database": {"path": "runtime.db"},
        "health_check": {"interval_seconds": 60},
    }
    settings = {
        "timeframe": "hourly",
        "context_poll_seconds": 300,
        "funding_poll_seconds": 3600,
        "bybit_ohlcv_seconds": 3600,
        "bybit_oi_seconds": 3600,
        "bybit_volume_seconds": 3600,
        "tick_interval_seconds": 3600,
        "signal_refresh_enabled": True,
        "signal_refresh_debounce_seconds": 2.0,
        "signal_refresh_max_pending_assets": 16,
        "cooldown_seconds": 3600,
        "telegram_min_interval_seconds": 10.0,
        "telegram_queue_size": 10,
        "freshness_mode": "warn",
        "freshness_thresholds": {},
    }

    class StubHLClient:
        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

    class StubBybitClient:
        def __init__(self, api_key: str = "", api_secret: str = "") -> None:
            self.api_key = api_key
            self.api_secret = api_secret

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

    class StubStore:
        def __init__(self, db_path: str) -> None:
            self.db_path = db_path

        def counts(self) -> dict:
            return {}

        def close(self) -> None:
            return None

    class StubSignal:
        name = "stub"

    class StubEngine:
        def __init__(self, config_dir: str, global_config: dict, store) -> None:
            self._signals = [StubSignal()]

    class StubAlertManager:
        def __init__(self, cooldown_seconds: int, cadence: str) -> None:
            self.total_alerts = 0

    async def fake_start(self) -> None:
        return None

    async def fake_stop(self) -> None:
        return None

    monkeypatch.setattr(signal_runtime, "load_global_config", lambda path: config)
    monkeypatch.setattr(signal_runtime, "validate_config", lambda global_config, dry_run: None)
    monkeypatch.setattr(signal_runtime, "resolve_runtime_settings", lambda global_config: settings)
    monkeypatch.setattr(signal_runtime, "load_signal_config", lambda config_dir, signal_name: {})
    monkeypatch.setattr(signal_runtime, "HLClient", StubHLClient)
    monkeypatch.setattr(signal_runtime, "BybitClient", StubBybitClient)
    monkeypatch.setattr(signal_runtime, "SQLiteDataStore", StubStore)
    monkeypatch.setattr(signal_runtime, "SignalEngine", StubEngine)
    monkeypatch.setattr(signal_runtime, "AlertManager", StubAlertManager)
    monkeypatch.setattr(signal_runtime.RuntimeSupervisor, "start", fake_start)
    monkeypatch.setattr(signal_runtime.RuntimeSupervisor, "stop", fake_stop)

    runtime = signal_runtime.SignalRuntime(backend_root=tmp_path, dry_run=True)
    asyncio.run(runtime.start())

    specs = runtime.supervisor._specs
    assert "poll_bybit_ohlcv" in specs
    assert "poll_hl_ohlcv" not in specs
    assert specs["poll_bybit_ohlcv"].critical is True
    assert specs["poll_bybit_oi"].critical is True
    assert specs["poll_bybit_volume"].critical is True

    asyncio.run(runtime.stop())
