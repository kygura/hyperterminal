"""
hl-signal-daemon — main entry point (v2).

Data sources:
  - Hyperliquid REST (funding history, asset snapshots) + WebSocket (live trades, liquidations)
  - Bybit REST (OHLCV 1h candles, open interest, spot volume) as the primary bulk context hydrator

Signal modules:
  - FundingExtremesSignal, OIVolumeDivergenceSignal, CVDDivergenceSignal, PremiumExtremesSignal
  - SpotLedFlowSignal (only when explicit spot volume is present), VWAPDeviationSignal (v2, modifier)
  - Websocket-fed signals also refresh on a debounced live-update path

Confluence engine: named-regime detection with conflict resolution.

Output:
  - Telegram: MEDIUM + HIGH conviction trade candidates
  - Terminal: all candidates (incl. LOW)

Usage:
    python main.py
    python main.py --dry-run
    python main.py --log-level DEBUG
    python main.py inspect --asset BTC --hours 24
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys
import time
from typing import Callable, Optional

import yaml
from dotenv import load_dotenv

from core.runtime import configure_logging, get_log_file
from data.hl_client.daemon_client import HLClient
from data.bybit_client import BybitClient
from db.paths import resolve_signal_db_path
from db.store import SQLiteDataStore
from engine.signal_engine import SignalEngine, TradeCandidate
from alerts import AlertManager
from telegram_bot import TelegramBot

_CONVICTION_RANK: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

_TIMEFRAME_PROFILES = {
    "hourly": {
        "context_poll_seconds": 300,
        "funding_poll_seconds": 3600,
        "bybit_ohlcv_seconds": 3600,
        "bybit_oi_seconds": 3600,
        "bybit_volume_seconds": 3600,
        "tick_interval_seconds": 3600,
        "cooldown_seconds": 3600,
    },
    "daily": {
        "context_poll_seconds": 1800,
        "funding_poll_seconds": 21600,
        "bybit_ohlcv_seconds": 21600,
        "bybit_oi_seconds": 21600,
        "bybit_volume_seconds": 21600,
        "tick_interval_seconds": 86400,
        "cooldown_seconds": 86400,
    },
    "weekly": {
        "context_poll_seconds": 21600,
        "funding_poll_seconds": 86400,
        "bybit_ohlcv_seconds": 86400,
        "bybit_oi_seconds": 86400,
        "bybit_volume_seconds": 86400,
        "tick_interval_seconds": 604800,
        "cooldown_seconds": 604800,
    },
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_level: str) -> None:
    configure_logging(default_level=log_level, default_log_file=get_log_file(os.path.join("logs", "daemon.log")))


logger = logging.getLogger("main")
load_dotenv()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_global_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def load_signal_config(config_dir: str, signal_name: str) -> dict:
    path = os.path.join(config_dir, "signals", f"{signal_name}.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def validate_config(global_config: dict, dry_run: bool) -> None:
    errors: list[str] = []

    required_keys = ["assets", "polling", "alerts", "engine", "confluence", "health_check"]
    for key in required_keys:
        if key not in global_config:
            errors.append(f"global.yaml missing required key: '{key}'")

    assets = global_config.get("assets", [])
    if not assets:
        errors.append("global.yaml: 'assets' must be a non-empty list")

    polling = global_config.get("polling", {})
    for field in ("funding_poll_seconds", "context_poll_seconds"):
        if field not in polling:
            errors.append(f"global.yaml: polling.{field} is required")

    if not dry_run:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or token in ("not_yet", "your_token_here"):
            errors.append("TELEGRAM_BOT_TOKEN not set (use --dry-run to skip Telegram)")
        if not chat_id or chat_id in ("not_yet", "your_chat_id_here"):
            errors.append("TELEGRAM_CHAT_ID not set (use --dry-run to skip Telegram)")

    freshness = global_config.get("freshness", {})
    freshness_mode = str(freshness.get("mode", "warn")).strip().lower()
    if freshness_mode not in {"off", "warn", "enforce"}:
        errors.append("global.yaml: freshness.mode must be one of off|warn|enforce")

    if errors:
        raise ValueError("; ".join(errors))

    logger.info("Config validation passed")


def resolve_runtime_settings(global_config: dict) -> dict:
    strategy = global_config.get("strategy", {})
    timeframe = str(strategy.get("timeframe", "hourly")).strip().lower()
    if timeframe not in _TIMEFRAME_PROFILES:
        timeframe = "hourly"

    polling = global_config.get("polling", {})
    engine = global_config.get("engine", {})
    alerts = global_config.get("alerts", {})
    freshness = global_config.get("freshness", {})
    profile = _TIMEFRAME_PROFILES[timeframe]
    signal_refresh_enabled = bool(engine.get("signal_refresh_enabled", True))

    def resolved(section: dict, key: str) -> int:
        configured = section.get(key)
        minimum = int(profile[key])
        if configured is None:
            return minimum
        return max(int(configured), minimum)

    return {
        "timeframe": timeframe,
        "context_poll_seconds": resolved(polling, "context_poll_seconds"),
        "funding_poll_seconds": resolved(polling, "funding_poll_seconds"),
        "bybit_ohlcv_seconds": resolved(polling, "bybit_ohlcv_seconds"),
        "bybit_oi_seconds": resolved(polling, "bybit_oi_seconds"),
        "bybit_volume_seconds": resolved(polling, "bybit_volume_seconds"),
        "tick_interval_seconds": resolved(engine, "tick_interval_seconds"),
        "signal_refresh_enabled": signal_refresh_enabled,
        "signal_refresh_debounce_seconds": float(engine.get("signal_refresh_debounce_seconds", 2.0)),
        "signal_refresh_max_pending_assets": max(int(engine.get("signal_refresh_max_pending_assets", 128)), 1),
        "cooldown_seconds": max(
            int(alerts.get("cooldown_seconds", profile["cooldown_seconds"])),
            int(profile["cooldown_seconds"]),
        ),
        "telegram_min_interval_seconds": max(
            float(alerts.get("telegram_min_interval_seconds", 10)),
            10.0,
        ),
        "telegram_queue_size": max(int(alerts.get("telegram_queue_size", 500)), 1),
        "freshness_mode": str(freshness.get("mode", "warn")).strip().lower(),
        "freshness_thresholds": {
            "asset_snapshots": max(int(freshness.get("asset_snapshot_max_age_seconds", 900)), 1),
            "funding_rates": max(int(freshness.get("funding_max_age_seconds", 7200)), 1),
            "open_interest": max(int(freshness.get("oi_max_age_seconds", 7200)), 1),
            "volume_snapshots": max(int(freshness.get("spot_volume_max_age_seconds", 7200)), 1),
            "ohlcv": max(int(freshness.get("ohlcv_max_age_seconds", 7200)), 1),
            "trade_ticks": max(int(freshness.get("trade_ticks_max_age_seconds", 900)), 1),
            "orderbook_snapshots": max(int(freshness.get("orderbook_max_age_seconds", 900)), 1),
            "liquidations": max(int(freshness.get("liquidations_max_age_seconds", 1800)), 1),
        },
    }


def serialize_candidate(candidate: TradeCandidate, timeframe: str) -> dict:
    price = candidate.vwap_state.get("current_price") if candidate.vwap_state else None
    vwap = candidate.vwap_state.get("vwap") if candidate.vwap_state else None
    return {
        "id": candidate.alert_id,
        "ts": int(candidate.timestamp * 1000),
        "asset": candidate.coin,
        "direction": candidate.direction,
        "regime": candidate.regime,
        "conviction": candidate.conviction,
        "signal_count": len(candidate.signals),
        "signals_json": [signal.signal_name for signal in candidate.signals],
        "price": price,
        "vwap": vwap,
        "timeframe": timeframe,
    }


def candidate_freshness_status(
    engine: SignalEngine,
    store: SQLiteDataStore,
    candidate: TradeCandidate,
    thresholds: dict[str, int],
) -> list[str]:
    latest = store.get_latest_timestamps(candidate.coin)
    required = engine.required_datasets_for_candidate(candidate)
    now_ms = int(time.time() * 1000)
    stale: list[str] = []
    for dataset in required:
        latest_ts = latest.get(dataset)
        max_age_seconds = thresholds.get(dataset)
        if max_age_seconds is None:
            continue
        if latest_ts is None or (now_ms - latest_ts) > (max_age_seconds * 1000):
            stale.append(dataset)
    return stale


# ---------------------------------------------------------------------------
# Background tasks — HL
# ---------------------------------------------------------------------------

async def poll_asset_contexts(
    client: HLClient,
    store: SQLiteDataStore,
    coins: list[str],
    interval_s: int,
    stop_event: asyncio.Event,
    on_update: Optional[Callable[[str], None]] = None,
) -> None:
    logger.info("Starting HL asset context polling every %ds", interval_s)
    while not stop_event.is_set():
        try:
            contexts = await client.get_asset_contexts()
            if contexts:
                for coin in coins:
                    ctx = contexts.get(coin)
                    if ctx:
                        store.add_snapshot(
                            coin=coin,
                            funding=ctx["fundingRate"],
                            oi=ctx["openInterest"],
                            mark_px=ctx["markPx"],
                            oracle_px=ctx["oraclePx"],
                            premium=ctx["premium"],
                            source="hyperliquid",
                        )
                        if on_update is not None:
                            on_update(coin)
            else:
                logger.warning("poll_asset_contexts: got None from HL API")
        except Exception as exc:
            logger.error("poll_asset_contexts error: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def poll_funding_history(
    client: HLClient,
    store: SQLiteDataStore,
    coins: list[str],
    interval_s: int,
    lookback_hours: int,
    stop_event: asyncio.Event,
    on_update: Optional[Callable[[str], None]] = None,
) -> None:
    logger.info("Starting HL funding history polling every %ds", interval_s)

    async def _poll_funding_one(coin: str, start_ms: int) -> None:
        try:
            entries = await client.get_funding_history(coin, start_ms)
            if entries:
                for entry in entries:
                    store.add_funding(
                        coin=coin,
                        rate=entry["fundingRate"],
                        premium=entry["premium"],
                        ts=entry["time"],
                        source="hyperliquid",
                    )
                if on_update is not None:
                    on_update(coin)
        except Exception as exc:
            logger.error("poll_funding_history error for %s: %s", coin, exc, exc_info=True)

    while not stop_event.is_set():
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - lookback_hours * 3600 * 1000

        results = await asyncio.gather(
            *[_poll_funding_one(coin, start_ms) for coin in coins],
            return_exceptions=True,
        )
        for coin, result in zip(coins, results):
            if isinstance(result, BaseException):
                logger.error("Unexpected error polling %s: %s", coin, result, exc_info=result)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def run_trades_ws(
    client: HLClient,
    store: SQLiteDataStore,
    coins: list[str],
    stop_event: asyncio.Event,
    on_update: Optional[Callable[[str], None]] = None,
) -> None:
    def on_trade(coin: str, side: str, px: float, sz: float, ts: int) -> None:
        store.add_trade(coin, side, px, sz, ts)
        if on_update is not None:
            on_update(coin)

    await client.connect_trades_ws(coins, on_trade, stop_event)


async def run_l2book_ws(
    client: HLClient,
    store: SQLiteDataStore,
    coins: list[str],
    stop_event: asyncio.Event,
    snapshot_interval_s: int = 30,
    depth_levels: int = 10,
    on_update: Optional[Callable[[str], None]] = None,
) -> None:
    last_persisted: dict[str, int] = {}

    def on_book(coin: str, bids: list[dict], asks: list[dict], ts: int) -> None:
        min_delta_ms = max(snapshot_interval_s, 1) * 1000
        if ts - last_persisted.get(coin, 0) < min_delta_ms:
            return
        store.add_orderbook_snapshot(
            coin=coin,
            ts=ts,
            bids=bids,
            asks=asks,
            depth_levels=depth_levels,
        )
        last_persisted[coin] = ts
        if on_update is not None:
            on_update(coin)

    await client.connect_l2book_ws(coins, on_book, stop_event)


async def run_liquidations_ws(
    client: HLClient,
    store: SQLiteDataStore,
    stop_event: asyncio.Event,
    on_update: Optional[Callable[[str], None]] = None,
) -> None:
    def on_liq(coin: str, side: str, px: float, sz: float, ts: int) -> None:
        store.add_liquidation(coin, side, px, sz, ts)
        if on_update is not None:
            on_update(coin)

    await client.connect_liquidations_ws(on_liq, stop_event)


async def prune_ticks_loop(
    store: SQLiteDataStore,
    retention_hours: int,
    interval_s: int,
    stop_event: asyncio.Event,
) -> None:
    retention_ms = retention_hours * 3600 * 1000
    while not stop_event.is_set():
        try:
            deleted = store.prune_old_ticks(retention_ms)
            if any(deleted.values()):
                logger.info("Pruned orderflow data: %s", deleted)
        except Exception as exc:
            logger.error("prune_ticks_loop error: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# Background tasks — Context hydration
# ---------------------------------------------------------------------------

async def poll_hl_ohlcv(
    client: HLClient,
    store: SQLiteDataStore,
    coins: list[str],
    interval_s: int,
    stop_event: asyncio.Event,
    on_update: Optional[Callable[[str], None]] = None,
) -> None:
    """Optional fallback OHLCV polling from Hyperliquid."""
    logger.info("Starting fallback HL OHLCV polling every %ds", interval_s)

    async def _poll_ohlcv_one(coin: str, start_ms: int) -> None:
        try:
            candles = await client.get_candle_snapshot(coin, "1h", start_ms)
            if candles:
                for c in candles:
                    store.add_ohlcv(
                        asset=coin,
                        ts=c["ts"],
                        open_=c["open"],
                        high=c["high"],
                        low=c["low"],
                        close=c["close"],
                        volume=c["volume"],
                        source="hyperliquid",
                        timeframe="1h",
                    )
                logger.debug("HL OHLCV: %d candles for %s", len(candles), coin)
                if on_update is not None:
                    on_update(coin)
        except Exception as exc:
            logger.error("poll_hl_ohlcv error for %s: %s", coin, exc, exc_info=True)

    # Start looking back 50 hours minimum
    while not stop_event.is_set():
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (50 * 3600 * 1000)

        results = await asyncio.gather(
            *[_poll_ohlcv_one(coin, start_ms) for coin in coins],
            return_exceptions=True,
        )
        for coin, result in zip(coins, results):
            if isinstance(result, BaseException):
                logger.error("Unexpected error polling %s: %s", coin, result, exc_info=result)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def poll_bybit_ohlcv(
    bybit: BybitClient,
    store: SQLiteDataStore,
    coins: list[str],
    interval_s: int,
    stop_event: asyncio.Event,
    on_update: Optional[Callable[[str], None]] = None,
) -> None:
    """Poll hourly OHLCV candles from Bybit as the primary context hydrator."""
    logger.info("Starting Bybit OHLCV polling every %ds", interval_s)

    async def _poll_ohlcv_one(coin: str) -> None:
        symbol = BybitClient.asset_to_symbol(coin)
        try:
            candles = await bybit.get_klines(symbol, interval="60", limit=50)
            if candles:
                for candle in candles:
                    store.add_ohlcv(
                        asset=coin,
                        ts=candle["ts"],
                        open_=candle["open"],
                        high=candle["high"],
                        low=candle["low"],
                        close=candle["close"],
                        volume=candle["volume"],
                        source="bybit",
                        timeframe="1h",
                    )
                logger.debug("Bybit OHLCV: %d candles for %s", len(candles), coin)
                if on_update is not None:
                    on_update(coin)
        except Exception as exc:
            logger.error("poll_bybit_ohlcv error for %s: %s", coin, exc, exc_info=True)

    while not stop_event.is_set():
        results = await asyncio.gather(
            *[_poll_ohlcv_one(coin) for coin in coins],
            return_exceptions=True,
        )
        for coin, result in zip(coins, results):
            if isinstance(result, BaseException):
                logger.error("Unexpected error polling %s: %s", coin, result, exc_info=result)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def poll_bybit_oi(
    bybit: BybitClient,
    store: SQLiteDataStore,
    coins: list[str],
    interval_s: int,
    stop_event: asyncio.Event,
    on_update: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Poll OI from Bybit every interval_s seconds.
    Bybit owns bulk context hydration; Hyperliquid snapshots still mirror
    venue-native OI for live market state.
    """
    logger.info("Starting Bybit OI polling every %ds", interval_s)

    async def _poll_oi_one(coin: str) -> None:
        symbol = BybitClient.asset_to_symbol(coin)
        try:
            readings = await bybit.get_open_interest(symbol, interval_time="1h", limit=50)
            for r in readings:
                store.add_oi(coin=coin, oi=r["oi"], ts=r["ts"], source="bybit")
            logger.debug("Bybit OI: %d readings for %s", len(readings), coin)
            if readings and on_update is not None:
                on_update(coin)
        except Exception as exc:
            logger.error("poll_bybit_oi error for %s: %s", coin, exc, exc_info=True)

    while not stop_event.is_set():
        results = await asyncio.gather(
            *[_poll_oi_one(coin) for coin in coins],
            return_exceptions=True,
        )
        for coin, result in zip(coins, results):
            if isinstance(result, BaseException):
                logger.error("Unexpected error polling %s: %s", coin, result, exc_info=result)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def poll_bybit_volume(
    bybit: BybitClient,
    store: SQLiteDataStore,
    coins: list[str],
    interval_s: int,
    stop_event: asyncio.Event,
    on_update: Optional[Callable[[str], None]] = None,
) -> None:
    """Poll spot volume from Bybit every interval_s seconds."""
    logger.info("Starting Bybit volume polling every %ds", interval_s)

    async def _poll_volume_one(coin: str) -> None:
        symbol = BybitClient.asset_to_symbol(coin, category="spot")
        try:
            spot_candles = await bybit.get_spot_volume(symbol, interval="60", limit=50)
            for c in spot_candles:
                turnover = c.get("turnover", c["volume"])
                store.add_volume_snapshot(
                    coin=coin,
                    ts=c["ts"],
                    futures_volume=0.0,
                    spot_volume=turnover,
                    source="bybit_spot",
                )
            logger.debug("Bybit spot vol: %d entries for %s", len(spot_candles), coin)
            if spot_candles and on_update is not None:
                on_update(coin)
        except Exception as exc:
            logger.error("poll_bybit_volume error for %s: %s", coin, exc, exc_info=True)

    while not stop_event.is_set():
        results = await asyncio.gather(
            *[_poll_volume_one(coin) for coin in coins],
            return_exceptions=True,
        )
        for coin, result in zip(coins, results):
            if isinstance(result, BaseException):
                logger.error("Unexpected error polling %s: %s", coin, result, exc_info=result)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# Engine tick loop
# ---------------------------------------------------------------------------

async def process_signal_candidates(
    engine: SignalEngine,
    alert_manager: AlertManager,
    store: SQLiteDataStore,
    telegram: Optional[TelegramBot],
    telegram_queue: Optional[asyncio.Queue[tuple[str, str]]],
    coins: list[str],
    dry_run: bool,
    timeframe: str,
    freshness_mode: str = "warn",
    freshness_thresholds: Optional[dict[str, int]] = None,
    on_candidate=None,
    min_conviction: Optional[str] = None,
) -> None:
    results = await engine.evaluate_all(coins)
    candidates = engine.score_confluence(results)
    freshness_thresholds = freshness_thresholds or {}

    if min_conviction is not None:
        if min_conviction not in _CONVICTION_RANK:
            raise ValueError(f"Unknown min_conviction: {min_conviction!r}")
        floor = _CONVICTION_RANK[min_conviction]
        suppressed = [
            candidate
            for candidate in candidates
            if _CONVICTION_RANK.get(candidate.conviction, 0) < floor
        ]
        candidates = [
            candidate
            for candidate in candidates
            if _CONVICTION_RANK.get(candidate.conviction, 0) >= floor
        ]
        if suppressed:
            logger.debug(
                "process_signal_candidates: suppressed %d below-%s candidate(s) for %s",
                len(suppressed),
                min_conviction,
                [candidate.coin for candidate in suppressed],
            )

    for candidate in candidates:
        if not alert_manager.should_fire(candidate):
            continue

        full_message = alert_manager.format_alert(candidate)
        send_telegram = alert_manager.should_telegram(candidate)
        stale_datasets = candidate_freshness_status(engine, store, candidate, freshness_thresholds)
        if stale_datasets:
            logger.warning(
                "Freshness check for %s failed: stale datasets=%s mode=%s",
                candidate.coin,
                stale_datasets,
                freshness_mode,
            )
            if freshness_mode == "enforce":
                continue

        delivery_bucket = alert_manager.delivery_bucket_for(candidate.timestamp)
        dedupe_key = alert_manager.candidate_fingerprint(candidate)

        try:
            inserted = store.add_trade_candidate(
                asset=candidate.coin,
                direction=candidate.direction,
                regime=candidate.regime,
                conviction=candidate.conviction,
                signal_names=[s.signal_name for s in candidate.signals],
                price=candidate.vwap_state.get("current_price") if candidate.vwap_state else None,
                vwap=candidate.vwap_state.get("vwap") if candidate.vwap_state else None,
                alert_sent=send_telegram and not dry_run,
                delivery_bucket=delivery_bucket,
                dedupe_key=dedupe_key,
            )
        except Exception as exc:
            logger.error("Failed to log trade candidate: %s", exc)
            continue

        if not inserted:
            logger.debug("Skipping duplicate trade candidate for %s bucket=%s", candidate.coin, delivery_bucket)
            continue

        print(f"\n{'='*60}\n{full_message}\n{'='*60}\n")
        logger.info(
            "Trade candidate: %s %s [%s] %s — %d signals",
            candidate.coin, candidate.direction, candidate.conviction,
            candidate.regime, len(candidate.signals),
        )

        if on_candidate is not None:
            try:
                await on_candidate(serialize_candidate(candidate, timeframe))
            except Exception as exc:
                logger.error("Failed to broadcast trade candidate: %s", exc)

        if send_telegram:
            if dry_run:
                logger.info(
                    "DRY-RUN: would send Telegram for %s %s [%s]",
                    candidate.coin, candidate.direction, candidate.conviction,
                )
            elif telegram and telegram_queue is not None:
                try:
                    telegram_queue.put_nowait((full_message, candidate.conviction))
                except asyncio.QueueFull:
                    logger.warning("Telegram queue full; dropping alert for %s", candidate.coin)

        alert_manager.record_fire(candidate)

async def engine_tick_loop(
    engine: SignalEngine,
    alert_manager: AlertManager,
    store: SQLiteDataStore,
    telegram: Optional[TelegramBot],
    telegram_queue: Optional[asyncio.Queue[tuple[str, str]]],
    coins: list[str],
    tick_interval_s: int,
    stop_event: asyncio.Event,
    dry_run: bool,
    timeframe: str,
    freshness_mode: str = "warn",
    freshness_thresholds: Optional[dict[str, int]] = None,
    on_candidate=None,
) -> None:
    logger.info("Starting engine tick loop every %ds", tick_interval_s)
    while not stop_event.is_set():
        try:
            await process_signal_candidates(
                engine=engine,
                alert_manager=alert_manager,
                store=store,
                telegram=telegram,
                telegram_queue=telegram_queue,
                coins=coins,
                dry_run=dry_run,
                timeframe=timeframe,
                freshness_mode=freshness_mode,
                freshness_thresholds=freshness_thresholds,
                on_candidate=on_candidate,
            )
        except Exception as exc:
            logger.error("engine_tick_loop error: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=tick_interval_s)
        except asyncio.TimeoutError:
            pass


async def signal_refresh_loop(
    engine: SignalEngine,
    alert_manager: AlertManager,
    store: SQLiteDataStore,
    telegram: Optional[TelegramBot],
    telegram_queue: Optional[asyncio.Queue[tuple[str, str]]],
    refresh_coordinator,
    debounce_seconds: float,
    stop_event: asyncio.Event,
    dry_run: bool,
    timeframe: str,
    freshness_mode: str = "warn",
    freshness_thresholds: Optional[dict[str, int]] = None,
    on_candidate=None,
) -> None:
    """
    Debounced refresh loop for websocket-fed updates.

    Signals triggered by live trades, liquidations, or orderbook changes should
    not wait for the hourly engine tick. This loop coalesces bursts of updates
    and evaluates only the impacted coins.
    """
    logger.info("Starting debounced signal refresh loop (debounce=%.1fs)", debounce_seconds)

    while not stop_event.is_set():
        try:
            pending = await refresh_coordinator.wait_for_batch(stop_event, debounce_seconds)
            if pending:
                try:
                    await process_signal_candidates(
                        engine=engine,
                        alert_manager=alert_manager,
                        store=store,
                        telegram=telegram,
                        telegram_queue=telegram_queue,
                        coins=sorted(pending),
                        dry_run=dry_run,
                        timeframe=timeframe,
                        freshness_mode=freshness_mode,
                        freshness_thresholds=freshness_thresholds,
                        on_candidate=on_candidate,
                        min_conviction="HIGH",
                    )
                except Exception as exc:
                    logger.error("signal_refresh_loop error: %s", exc, exc_info=True)
        except asyncio.TimeoutError:
            continue


async def telegram_delivery_loop(
    telegram: Optional[TelegramBot],
    telegram_queue: Optional[asyncio.Queue[tuple[str, str]]],
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            if telegram is None or telegram_queue is None:
                await asyncio.wait_for(stop_event.wait(), timeout=0.5)
                continue
            message, priority = await asyncio.wait_for(telegram_queue.get(), timeout=0.5)
            try:
                await telegram.send_alert(message, priority)
            except Exception as exc:
                logger.error("telegram_delivery_loop error: %s", exc, exc_info=True)
        except asyncio.TimeoutError:
            continue


# ---------------------------------------------------------------------------
# Health check + log counts
# ---------------------------------------------------------------------------

async def log_data_counts(
    store: SQLiteDataStore,
    interval_s: int,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        counts = store.counts()
        logger.info("DataStore counts: %s", counts)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def health_check_loop(
    telegram: Optional[TelegramBot],
    alert_manager: AlertManager,
    store: SQLiteDataStore,
    interval_s: int,
    start_time: float,
    stop_event: asyncio.Event,
    dry_run: bool,
    health_provider: Optional[Callable[[], dict]] = None,
) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
    except asyncio.TimeoutError:
        pass

    while not stop_event.is_set():
        try:
            uptime_s = int(time.time() - start_time)
            counts = store.counts()
            runtime_snapshot = health_provider() if health_provider else None
            if dry_run or not telegram:
                logger.info(
                    "Health check: uptime=%ds alerts=%d status=%s counts=%s",
                    uptime_s,
                    alert_manager.total_alerts,
                    runtime_snapshot.get("status") if runtime_snapshot else "unknown",
                    counts,
                )
            else:
                await telegram.send_health_check(
                    uptime_s=uptime_s,
                    total_alerts=alert_manager.total_alerts,
                    data_counts=counts,
                    runtime_snapshot=runtime_snapshot,
                )
        except Exception as exc:
            logger.error("health_check_loop error: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# CLI: inspect command
# ---------------------------------------------------------------------------

def cmd_inspect(args: argparse.Namespace) -> None:
    """Print recent data from SQLite for a given asset."""
    store = SQLiteDataStore()
    asset = args.asset.upper()
    hours = args.hours
    lookback_ms = hours * 3600 * 1000

    print(f"\n=== DataStore Inspection: {asset} (last {hours}h) ===\n")

    print("--- Funding Rates ---")
    funding = store.get_funding_window(asset, lookback_ms)
    if funding:
        for f in funding[-10:]:
            ts_str = time.strftime("%Y-%m-%d %H:%M", time.gmtime(f["time"] / 1000))
            print(f"  [{ts_str}] rate={f['rate']:.6f}")
    else:
        print("  (no data)")

    print("\n--- OI Change ---")
    oi = store.get_oi_change(asset, lookback_ms)
    print(f"  start={oi['oi_start']:.2f} end={oi['oi_end']:.2f} change={oi['oi_pct_change']:+.2f}%")

    print("\n--- Volume Summary ---")
    vol = store.get_volume_summary(asset, lookback_ms)
    print(f"  buy={vol['buy_volume']:.2f} sell={vol['sell_volume']:.2f} spot={vol['spot_volume']:.2f}")

    print("\n--- OHLCV (last 5 candles) ---")
    ohlcv = store.get_ohlcv_window(asset, lookback_ms)
    if ohlcv:
        for c in ohlcv[-5:]:
            ts_str = time.strftime("%Y-%m-%d %H:%M", time.gmtime(c["ts"] / 1000))
            print(f"  [{ts_str}] O={c['open']:.2f} H={c['high']:.2f} L={c['low']:.2f} C={c['close']:.2f} V={c['volume']:.2f} VWAP={c.get('vwap', 0):.2f}")
    else:
        print("  (no data)")

    print("\n--- Session VWAP ---")
    vwap = store.get_session_vwap(asset)
    print(f"  {vwap:.2f}" if vwap else "  (not enough OHLCV data)")

    print("\n--- Recent Trade Candidates ---")
    candidates = store.get_recent_candidates(asset, limit=5)
    if candidates:
        for c in candidates:
            ts_str = time.strftime("%Y-%m-%d %H:%M", time.gmtime(c["ts"] / 1000))
            print(f"  [{ts_str}] {c['direction']} {c['regime']} [{c['conviction']}] signals={c['signals_json']}")
    else:
        print("  (none yet)")

    print(f"\n--- Total Counts ---")
    counts = store.counts()
    for k, v in counts.items():
        print(f"  {k}: {v}")

    store.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    dry_run = args.dry_run

    setup_logging(args.log_level)
    logger.info("hl-signal-daemon v2 starting (dry_run=%s)", dry_run)
    from runtime.signal_runtime import SignalRuntime

    runtime = SignalRuntime(backend_root=os.path.dirname(__file__), dry_run=dry_run)

    def _handle_shutdown(sig: int, frame) -> None:
        logger.info("Received signal %s — initiating graceful shutdown", sig)
        runtime.stop_event.set()

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    try:
        await runtime.start()
        await runtime.stop_event.wait()
    finally:
        logger.info("Shutting down...")
        await runtime.stop()
        logger.info("hl-signal-daemon stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="hl-signal-daemon v2 — Signal Engine")
    subparsers = parser.add_subparsers(dest="command")

    # run (default)
    run_parser = parser.add_argument_group("run options")
    run_parser.add_argument("--dry-run", action="store_true",
                            help="Print alerts to console; skip Telegram")
    run_parser.add_argument("--log-level", default="INFO",
                            choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    # inspect
    inspect_parser = subparsers.add_parser("inspect", help="Inspect SQLite data store")
    inspect_parser.add_argument("--asset", default="BTC", help="Asset to inspect")
    inspect_parser.add_argument("--hours", type=int, default=24, help="Lookback hours")

    args = parser.parse_args()

    if args.command == "inspect":
        load_dotenv()
        cmd_inspect(args)
    else:
        asyncio.run(main(args))
