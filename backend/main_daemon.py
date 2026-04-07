"""
hl-signal-daemon — main entry point (v2).

Data sources:
  - Hyperliquid REST (funding history, asset snapshots) + WebSocket (live trades, liquidations)
  - Bybit REST (OHLCV 1h candles, open interest, spot volume) — scheduled hourly

Signal modules (all run each tick):
  - FundingExtremesSignal, OIVolumeDivergenceSignal, CVDDivergenceSignal, PremiumExtremesSignal
  - SpotLedFlowSignal (v2), VWAPDeviationSignal (v2, modifier)

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
from typing import Optional

import yaml
from dotenv import load_dotenv

from data.hl_client.daemon_client import HLClient
from data.bybit_client import BybitClient
from db.store import SQLiteDataStore
from engine.signal_engine import SignalEngine, TradeCandidate
from alerts import AlertManager
from telegram_bot import TelegramBot

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
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(numeric_level)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(numeric_level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    os.makedirs("logs", exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        "logs/daemon.log", maxBytes=10 * 1024 * 1024, backupCount=5
    )
    fh.setLevel(numeric_level)
    fh.setFormatter(fmt)
    root.addHandler(fh)


logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_global_config(path: str) -> dict:
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

    if errors:
        for e in errors:
            print(f"[CONFIG ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    logger.info("Config validation passed")


def resolve_runtime_settings(global_config: dict) -> dict:
    strategy = global_config.get("strategy", {})
    timeframe = str(strategy.get("timeframe", "hourly")).strip().lower()
    if timeframe not in _TIMEFRAME_PROFILES:
        timeframe = "hourly"

    polling = global_config.get("polling", {})
    engine = global_config.get("engine", {})
    alerts = global_config.get("alerts", {})
    profile = _TIMEFRAME_PROFILES[timeframe]

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
        "cooldown_seconds": max(
            int(alerts.get("cooldown_seconds", profile["cooldown_seconds"])),
            int(profile["cooldown_seconds"]),
        ),
        "telegram_min_interval_seconds": max(
            float(alerts.get("telegram_min_interval_seconds", 10)),
            10.0,
        ),
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


# ---------------------------------------------------------------------------
# Background tasks — HL
# ---------------------------------------------------------------------------

async def poll_asset_contexts(
    client: HLClient,
    store: SQLiteDataStore,
    coins: list[str],
    interval_s: int,
    stop_event: asyncio.Event,
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
) -> None:
    logger.info("Starting HL funding history polling every %ds", interval_s)
    while not stop_event.is_set():
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - lookback_hours * 3600 * 1000
        for coin in coins:
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
            except Exception as exc:
                logger.error("poll_funding_history error for %s: %s", coin, exc, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def run_trades_ws(
    client: HLClient,
    store: SQLiteDataStore,
    coins: list[str],
    stop_event: asyncio.Event,
) -> None:
    def on_trade(coin: str, side: str, px: float, sz: float, ts: int) -> None:
        store.add_trade(coin, side, px, sz, ts)

    await client.connect_trades_ws(coins, on_trade, stop_event)


async def run_liquidations_ws(
    client: HLClient,
    store: SQLiteDataStore,
    stop_event: asyncio.Event,
) -> None:
    def on_liq(coin: str, side: str, px: float, sz: float, ts: int) -> None:
        store.add_liquidation(coin, side, px, sz, ts)

    await client.connect_liquidations_ws(on_liq, stop_event)


# ---------------------------------------------------------------------------
# Background tasks — Bybit REST
# ---------------------------------------------------------------------------

async def poll_hl_ohlcv(
    client: HLClient,
    store: SQLiteDataStore,
    coins: list[str],
    interval_s: int,
    stop_event: asyncio.Event,
) -> None:
    """Poll hourly OHLCV candles from Hyperliquid every interval_s seconds."""
    logger.info("Starting HL OHLCV polling every %ds", interval_s)
    # Start looking back 50 hours minimum
    while not stop_event.is_set():
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (50 * 3600 * 1000)
        for coin in coins:
            try:
                # '1h' interval
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
            except Exception as exc:
                logger.error("poll_hl_ohlcv error for %s: %s", coin, exc, exc_info=True)

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
) -> None:
    """
    Poll OI from Bybit every interval_s seconds.
    This acts as a fallback/secondary source. HL provides primary real-time OI 
    via metaAndAssetCtxs.
    """
    logger.info("Starting Bybit OI polling (fallback) every %ds", interval_s)
    while not stop_event.is_set():
        for coin in coins:
            symbol = BybitClient.asset_to_symbol(coin)
            try:
                readings = await bybit.get_open_interest(symbol, interval_time="1h", limit=50)
                for r in readings:
                    store.add_oi(coin=coin, oi=r["oi"], ts=r["ts"], source="bybit")
                logger.debug("Bybit OI: %d readings for %s", len(readings), coin)
            except Exception as exc:
                logger.error("poll_bybit_oi error for %s: %s", coin, exc, exc_info=True)

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
) -> None:
    """Poll spot volume from Bybit every interval_s seconds."""
    logger.info("Starting Bybit volume polling every %ds", interval_s)
    while not stop_event.is_set():
        for coin in coins:
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
            except Exception as exc:
                logger.error("poll_bybit_volume error for %s: %s", coin, exc, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# Engine tick loop
# ---------------------------------------------------------------------------

async def engine_tick_loop(
    engine: SignalEngine,
    alert_manager: AlertManager,
    store: SQLiteDataStore,
    telegram: Optional[TelegramBot],
    coins: list[str],
    tick_interval_s: int,
    stop_event: asyncio.Event,
    dry_run: bool,
    timeframe: str,
    on_candidate=None,
) -> None:
    logger.info("Starting engine tick loop every %ds", tick_interval_s)
    while not stop_event.is_set():
        try:
            results = await engine.evaluate_all(coins)
            candidates = engine.score_confluence(results)

            for candidate in candidates:
                if not alert_manager.should_fire(candidate):
                    continue

                alert_manager.record_fire(candidate)
                full_message = alert_manager.format_alert(candidate)
                send_telegram = alert_manager.should_telegram(candidate)

                # Always print to terminal
                print(f"\n{'='*60}\n{full_message}\n{'='*60}\n")
                logger.info(
                    "Trade candidate: %s %s [%s] %s — %d signals",
                    candidate.coin, candidate.direction, candidate.conviction,
                    candidate.regime, len(candidate.signals),
                )

                # Log to SQLite
                try:
                    store.add_trade_candidate(
                        asset=candidate.coin,
                        direction=candidate.direction,
                        regime=candidate.regime,
                        conviction=candidate.conviction,
                        signal_names=[s.signal_name for s in candidate.signals],
                        price=candidate.vwap_state.get("current_price") if candidate.vwap_state else None,
                        vwap=candidate.vwap_state.get("vwap") if candidate.vwap_state else None,
                        alert_sent=send_telegram and not dry_run,
                    )
                except Exception as exc:
                    logger.error("Failed to log trade candidate: %s", exc)

                if on_candidate is not None:
                    try:
                        await on_candidate(serialize_candidate(candidate, timeframe))
                    except Exception as exc:
                        logger.error("Failed to broadcast trade candidate: %s", exc)

                # Send Telegram for MEDIUM/HIGH
                if send_telegram:
                    if dry_run:
                        logger.info("DRY-RUN: would send Telegram for %s %s [%s]",
                                    candidate.coin, candidate.direction, candidate.conviction)
                    elif telegram:
                        await telegram.send_alert(full_message, candidate.conviction)

        except Exception as exc:
            logger.error("engine_tick_loop error: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=tick_interval_s)
        except asyncio.TimeoutError:
            pass


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
) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
    except asyncio.TimeoutError:
        pass

    while not stop_event.is_set():
        try:
            uptime_s = int(time.time() - start_time)
            counts = store.counts()
            if dry_run or not telegram:
                logger.info(
                    "Health check: uptime=%ds alerts=%d counts=%s",
                    uptime_s, alert_manager.total_alerts, counts,
                )
            else:
                await telegram.send_health_check(
                    uptime_s=uptime_s,
                    total_alerts=alert_manager.total_alerts,
                    data_counts=counts,
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
    start_time = time.time()
    dry_run = args.dry_run

    setup_logging(args.log_level)
    logger.info("hl-signal-daemon v2 starting (dry_run=%s)", dry_run)

    load_dotenv()

    global_config = load_global_config(os.path.join("config", "global.yaml"))
    validate_config(global_config, dry_run)

    coins: list[str] = global_config["assets"]
    runtime_settings = resolve_runtime_settings(global_config)
    context_poll_s = runtime_settings["context_poll_seconds"]
    funding_poll_s = runtime_settings["funding_poll_seconds"]
    bybit_ohlcv_s = runtime_settings["bybit_ohlcv_seconds"]
    bybit_oi_s = runtime_settings["bybit_oi_seconds"]
    bybit_vol_s = runtime_settings["bybit_volume_seconds"]
    tick_s = runtime_settings["tick_interval_seconds"]
    cooldown_s = runtime_settings["cooldown_seconds"]
    health_s: int = global_config.get("health_check", {}).get("interval_seconds", 21600)
    lookback_hours: int = 48

    db_path = global_config.get("database", {}).get("path", "data.db")

    # Initialise components
    hl_client = HLClient()
    bybit_client = BybitClient(
        api_key=os.getenv("BYBIT_API_KEY", ""),
        api_secret=os.getenv("BYBIT_API_SECRET", ""),
    )
    store = SQLiteDataStore(db_path=db_path)
    engine = SignalEngine(config_dir="config", global_config=global_config, store=store)
    alert_manager = AlertManager(
        cooldown_seconds=cooldown_s,
        cadence=runtime_settings["timeframe"],
    )

    telegram: Optional[TelegramBot] = None
    if not dry_run:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if token and token not in ("not_yet",):
            telegram = TelegramBot(
                token=token,
                chat_id=chat_id,
                min_interval_seconds=runtime_settings["telegram_min_interval_seconds"],
            )

    await hl_client.start()
    await bybit_client.start()

    enabled_signals = [s.name for s in engine._signals]
    if telegram:
        await telegram.send_startup_message(assets=coins, signals=enabled_signals)
    logger.info("Daemon online — assets=%s signals=%s db=%s", coins, enabled_signals, db_path)

    stop_event = asyncio.Event()

    def _handle_shutdown(sig: int, frame) -> None:
        logger.info("Received signal %s — initiating graceful shutdown", sig)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    tasks = [
        # HL data
        asyncio.create_task(
            poll_asset_contexts(hl_client, store, coins, context_poll_s, stop_event),
            name="poll_hl_context",
        ),
        asyncio.create_task(
            poll_funding_history(hl_client, store, coins, funding_poll_s, lookback_hours, stop_event),
            name="poll_hl_funding",
        ),
        asyncio.create_task(
            run_trades_ws(hl_client, store, coins, stop_event),
            name="ws_trades",
        ),
        asyncio.create_task(
            run_liquidations_ws(hl_client, store, stop_event),
            name="ws_liquidations",
        ),
        # Bybit REST pollers
        asyncio.create_task(
            poll_hl_ohlcv(hl_client, store, coins, bybit_ohlcv_s, stop_event),
            name="poll_hl_ohlcv",
        ),
        asyncio.create_task(
            poll_bybit_oi(bybit_client, store, coins, bybit_oi_s, stop_event),
            name="poll_bybit_oi",
        ),
        asyncio.create_task(
            poll_bybit_volume(bybit_client, store, coins, bybit_vol_s, stop_event),
            name="poll_bybit_volume",
        ),
        # Engine
        asyncio.create_task(
            engine_tick_loop(
                engine,
                alert_manager,
                store,
                telegram,
                coins,
                tick_s,
                stop_event,
                dry_run,
                runtime_settings["timeframe"],
            ),
            name="engine_tick",
        ),
        # Diagnostics
        asyncio.create_task(
            log_data_counts(store, 60, stop_event),
            name="log_counts",
        ),
        asyncio.create_task(
            health_check_loop(telegram, alert_manager, store, health_s, start_time, stop_event, dry_run),
            name="health_check",
        ),
    ]

    def _task_done_callback(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("Task %s raised: %s", task.get_name(), task.exception())

    for task in tasks:
        task.add_done_callback(_task_done_callback)

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Unhandled exception in gather: %s", exc, exc_info=True)
    finally:
        logger.info("Shutting down...")
        for task in tasks:
            task.cancel()

        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Shutdown timeout — forcing exit")

        await hl_client.close()
        await bybit_client.close()
        store.close()

        if telegram:
            try:
                await telegram.send_shutdown_message()
            except Exception:
                pass

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
