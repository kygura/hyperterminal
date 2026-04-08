from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from dotenv import load_dotenv

from alerts import AlertManager
from data.bybit_client import BybitClient
from data.hl_client.daemon_client import HLClient
from db.store import SQLiteDataStore
from engine.signal_engine import SignalEngine
from main_daemon import (
    engine_tick_loop,
    health_check_loop,
    load_global_config,
    load_signal_config,
    log_data_counts,
    poll_asset_contexts,
    poll_bybit_oi,
    poll_bybit_volume,
    poll_funding_history,
    poll_hl_ohlcv,
    prune_ticks_loop,
    resolve_runtime_settings,
    run_l2book_ws,
    run_liquidations_ws,
    run_trades_ws,
    signal_refresh_loop,
    telegram_delivery_loop,
    validate_config,
)
from runtime.supervisor import RuntimeSupervisor, TaskSpec
from telegram_bot import TelegramBot

logger = logging.getLogger(__name__)


class SignalRefreshCoordinator:
    def __init__(self, coins: list[str], max_pending_assets: int = 128) -> None:
        self._all_coins = sorted(coins)
        self._pending: set[str] = set()
        self._event = asyncio.Event()
        self._overflowed = False
        self._overflow_count = 0
        self._max_pending_assets = max(1, int(max_pending_assets))

    def mark_dirty(self, coin: str) -> None:
        if coin:
            self._pending.add(coin)
        if len(self._pending) > self._max_pending_assets:
            self._overflowed = True
            self._overflow_count += 1
        self._event.set()

    async def wait_for_batch(self, stop_event: asyncio.Event, debounce_seconds: float) -> list[str]:
        while not stop_event.is_set():
            await asyncio.wait_for(self._event.wait(), timeout=0.5)
            await asyncio.sleep(max(0.0, debounce_seconds))
            batch = self._all_coins if self._overflowed else sorted(self._pending)
            self._pending.clear()
            self._overflowed = False
            self._event.clear()
            if batch:
                return batch
        return []

    def snapshot(self) -> dict:
        return {
            "pending_assets": sorted(self._pending),
            "pending_count": len(self._pending),
            "overflow_count": self._overflow_count,
        }


class SignalRuntime:
    def __init__(
        self,
        *,
        backend_root: str | Path,
        dry_run: bool = False,
        on_candidate: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> None:
        self.backend_root = Path(backend_root)
        self.dry_run = dry_run
        self.on_candidate = on_candidate
        self.stop_event = asyncio.Event()
        self.supervisor = RuntimeSupervisor(stop_event=self.stop_event)
        self.start_time = time.time()

        self.global_config: dict = {}
        self.runtime_settings: dict = {}
        self.coins: list[str] = []
        self.health_interval_seconds = 21600
        self.orderflow_retention_hours = 48
        self.orderbook_snapshot_seconds = 30
        self.orderbook_depth_levels = 10

        self.hl_client: Optional[HLClient] = None
        self.bybit_client: Optional[BybitClient] = None
        self.store: Optional[SQLiteDataStore] = None
        self.engine: Optional[SignalEngine] = None
        self.alert_manager: Optional[AlertManager] = None
        self.telegram: Optional[TelegramBot] = None
        self.refresh_coordinator: Optional[SignalRefreshCoordinator] = None
        self.telegram_queue: Optional[asyncio.Queue[tuple[str, str]]] = None

    async def start(self) -> None:
        load_dotenv()
        self.global_config = load_global_config(str(self.backend_root / "config" / "global.yaml"))
        telegram_enabled = bool(
            (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
            and (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
        )
        validate_config(self.global_config, dry_run=self.dry_run or not telegram_enabled)
        self.runtime_settings = resolve_runtime_settings(self.global_config)
        orderbook_config = load_signal_config(str(self.backend_root / "config"), "orderbook_imbalance")
        trade_flow_config = load_signal_config(str(self.backend_root / "config"), "trade_flow_imbalance")

        self.coins = self.global_config["assets"]
        self.health_interval_seconds = int(self.global_config.get("health_check", {}).get("interval_seconds", 21600))
        self.orderbook_snapshot_seconds = int(orderbook_config.get("snapshot_interval_seconds", 30))
        self.orderbook_depth_levels = int(orderbook_config.get("depth_levels", 10))
        self.orderflow_retention_hours = int(trade_flow_config.get("retention_hours", 48))
        db_path = str(self.backend_root / self.global_config.get("database", {}).get("path", "data.db"))

        self.hl_client = HLClient()
        self.bybit_client = BybitClient(
            api_key=os.getenv("BYBIT_API_KEY", ""),
            api_secret=os.getenv("BYBIT_API_SECRET", ""),
        )
        self.store = SQLiteDataStore(db_path=db_path)
        self.engine = SignalEngine(
            config_dir=str(self.backend_root / "config"),
            global_config=self.global_config,
            store=self.store,
        )
        self.alert_manager = AlertManager(
            cooldown_seconds=self.runtime_settings["cooldown_seconds"],
            cadence=self.runtime_settings["timeframe"],
        )
        if telegram_enabled and not self.dry_run:
            self.telegram = TelegramBot(
                token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
                min_interval_seconds=self.runtime_settings["telegram_min_interval_seconds"],
            )

        await self.hl_client.start()
        await self.bybit_client.start()

        if self.runtime_settings["signal_refresh_enabled"]:
            self.refresh_coordinator = SignalRefreshCoordinator(
                self.coins,
                max_pending_assets=self.runtime_settings["signal_refresh_max_pending_assets"],
            )
        self.telegram_queue = asyncio.Queue(maxsize=self.runtime_settings["telegram_queue_size"])

        on_update = self.refresh_coordinator.mark_dirty if self.refresh_coordinator is not None else None

        self.supervisor.add(TaskSpec("poll_hl_context", lambda: poll_asset_contexts(
            self.hl_client, self.store, self.coins, self.runtime_settings["context_poll_seconds"], self.stop_event, on_update=on_update
        ), critical=True))
        self.supervisor.add(TaskSpec("poll_hl_funding", lambda: poll_funding_history(
            self.hl_client, self.store, self.coins, self.runtime_settings["funding_poll_seconds"], 48, self.stop_event, on_update=on_update
        ), critical=True))
        self.supervisor.add(TaskSpec("ws_trades", lambda: run_trades_ws(
            self.hl_client, self.store, self.coins, self.stop_event, on_update=on_update
        ), critical=True))
        self.supervisor.add(TaskSpec("ws_l2book", lambda: run_l2book_ws(
            self.hl_client, self.store, self.coins, self.stop_event,
            snapshot_interval_s=self.orderbook_snapshot_seconds,
            depth_levels=self.orderbook_depth_levels,
            on_update=on_update,
        ), critical=True))
        self.supervisor.add(TaskSpec("ws_liquidations", lambda: run_liquidations_ws(
            self.hl_client, self.store, self.stop_event, on_update=on_update
        ), critical=False))
        self.supervisor.add(TaskSpec("poll_hl_ohlcv", lambda: poll_hl_ohlcv(
            self.hl_client, self.store, self.coins, self.runtime_settings["bybit_ohlcv_seconds"], self.stop_event, on_update=on_update
        ), critical=True))
        self.supervisor.add(TaskSpec("poll_bybit_oi", lambda: poll_bybit_oi(
            self.bybit_client, self.store, self.coins, self.runtime_settings["bybit_oi_seconds"], self.stop_event, on_update=on_update
        ), critical=False))
        self.supervisor.add(TaskSpec("poll_bybit_volume", lambda: poll_bybit_volume(
            self.bybit_client, self.store, self.coins, self.runtime_settings["bybit_volume_seconds"], self.stop_event, on_update=on_update
        ), critical=False))
        self.supervisor.add(TaskSpec("engine_tick", lambda: engine_tick_loop(
            self.engine, self.alert_manager, self.store, self.telegram, self.telegram_queue,
            self.coins, self.runtime_settings["tick_interval_seconds"], self.stop_event,
            self.dry_run, self.runtime_settings["timeframe"], self.on_candidate,
        ), critical=True))
        if self.refresh_coordinator is not None:
            self.supervisor.add(TaskSpec("signal_refresh", lambda: signal_refresh_loop(
                self.engine, self.alert_manager, self.store, self.telegram, self.telegram_queue,
                self.refresh_coordinator, self.runtime_settings["signal_refresh_debounce_seconds"],
                self.stop_event, self.dry_run, self.runtime_settings["timeframe"], self.on_candidate,
            ), critical=True))
        self.supervisor.add(TaskSpec("telegram_delivery", lambda: telegram_delivery_loop(
            self.telegram, self.telegram_queue, self.stop_event
        ), critical=False))
        self.supervisor.add(TaskSpec("log_counts", lambda: log_data_counts(
            self.store, 60, self.stop_event
        ), critical=False))
        self.supervisor.add(TaskSpec("health_check", lambda: health_check_loop(
            self.telegram, self.alert_manager, self.store, self.health_interval_seconds,
            self.start_time, self.stop_event, self.dry_run, health_provider=self.snapshot
        ), critical=False))
        self.supervisor.add(TaskSpec("prune_orderflow_ticks", lambda: prune_ticks_loop(
            self.store, self.orderflow_retention_hours, 3600, self.stop_event
        ), critical=False))

        await self.supervisor.start()
        if self.telegram:
            await self.telegram.send_startup_message(
                assets=self.coins,
                signals=[signal.name for signal in self.engine._signals],
            )

    async def stop(self) -> None:
        await self.supervisor.stop()
        if self.hl_client:
            await self.hl_client.close()
            self.hl_client = None
        if self.bybit_client:
            await self.bybit_client.close()
            self.bybit_client = None
        if self.store:
            self.store.close()
            self.store = None
        if self.telegram:
            try:
                await self.telegram.send_shutdown_message()
            except Exception:
                logger.debug("Skipping telegram shutdown message", exc_info=True)
            self.telegram = None

    def snapshot(self) -> dict:
        counts = self.store.counts() if self.store else {}
        snapshot = self.supervisor.snapshot()
        snapshot.update(
            {
                "timeframe": self.runtime_settings.get("timeframe"),
                "dry_run": self.dry_run,
                "coins": self.coins,
                "refresh": self.refresh_coordinator.snapshot() if self.refresh_coordinator else {"pending_count": 0, "overflow_count": 0},
                "telegram_queue_depth": self.telegram_queue.qsize() if self.telegram_queue else 0,
                "total_alerts": self.alert_manager.total_alerts if self.alert_manager else 0,
                "data_counts": counts,
            }
        )
        return snapshot
