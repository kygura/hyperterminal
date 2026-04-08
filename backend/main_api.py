import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select

from api.branches import router as branches_router
from api.news import router as news_router, start_news_polling
from api.routes import router
from api.signals import router as signals_router
from api.telegram import router as telegram_router
from alerts import AlertManager
from data.bybit_client import BybitClient
from data.hl_client.client import HyperliquidClient
from data.hl_client.daemon_client import HLClient
from db.models import Wallet
from db.session import create_db_and_tables, engine
from db.store import SQLiteDataStore
from engine.handler import handle_update
from engine.signal_engine import SignalEngine
from engine.watcher import watcher
from main_daemon import (
    engine_tick_loop,
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
    validate_config,
    health_check_loop,
)
from telegram_bot import TelegramBot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parent
PRICE_UPDATE_INTERVAL = int(os.getenv("PRICE_UPDATE_INTERVAL_SECONDS", "300"))

app = FastAPI(title="Hypertrade API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        stale_connections: list[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as exc:
                logger.error("Error broadcasting: %s", exc)
                stale_connections.append(connection)

        for connection in stale_connections:
            self.disconnect(connection)


class IntervalUpdate(BaseModel):
    interval_seconds: int


manager = ConnectionManager()
signal_manager = ConnectionManager()
shared_client: Optional[HyperliquidClient] = None
price_task: Optional[asyncio.Task] = None
news_task: Optional[asyncio.Task] = None
signal_tasks: list[asyncio.Task] = []
signal_stop_event: Optional[asyncio.Event] = None
signal_store: Optional[SQLiteDataStore] = None
signal_hl_client: Optional[HLClient] = None
signal_bybit_client: Optional[BybitClient] = None
signal_telegram: Optional[TelegramBot] = None


async def broadcast_handler(address: str, data: dict):
    await handle_update(address, data)
    await manager.broadcast({"address": address, "data": data})


async def broadcast_prices_periodically():
    global shared_client
    if not shared_client:
        return

    assets = ["BTC", "ETH", "SOL", "HYPE"]

    while True:
        try:
            meta, asset_ctxs = await shared_client.get_meta_and_asset_ctxs()
            prices_data = {}

            if meta and asset_ctxs:
                universe = meta.get("universe", [])
                for index, ctx in enumerate(asset_ctxs):
                    if index < len(universe) and universe[index]["name"] in assets:
                        prices_data[universe[index]["name"]] = ctx

            if prices_data:
                await manager.broadcast(
                    {
                        "type": "prices",
                        "data": prices_data,
                        "timestamp": asyncio.get_event_loop().time(),
                    }
                )
        except Exception as exc:
            logger.error("Error fetching/broadcasting prices: %s", exc)

        await asyncio.sleep(PRICE_UPDATE_INTERVAL)


async def fetch_and_broadcast_prices_once():
    global shared_client
    if not shared_client:
        return

    assets = ["BTC", "ETH", "SOL", "HYPE"]

    try:
        meta, asset_ctxs = await shared_client.get_meta_and_asset_ctxs()
        prices_data = {}

        if meta and asset_ctxs:
            universe = meta.get("universe", [])
            for index, ctx in enumerate(asset_ctxs):
                if index < len(universe) and universe[index]["name"] in assets:
                    prices_data[universe[index]["name"]] = ctx

        if prices_data:
            await manager.broadcast(
                {
                    "type": "prices",
                    "data": prices_data,
                    "timestamp": asyncio.get_event_loop().time(),
                }
            )
    except Exception as exc:
        logger.error("Failed to fetch initial prices: %s", exc)


async def _broadcast_signal(payload: dict) -> None:
    await signal_manager.broadcast({"type": "signal", "data": payload})


async def start_signal_runtime() -> None:
    global signal_tasks, signal_stop_event, signal_store
    global signal_hl_client, signal_bybit_client, signal_telegram

    load_dotenv()
    global_config = load_global_config(str(BACKEND_ROOT / "config" / "global.yaml"))

    telegram_enabled = bool(
        (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        and (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    )
    validate_config(global_config, dry_run=not telegram_enabled)
    runtime = resolve_runtime_settings(global_config)
    orderbook_config = load_signal_config(str(BACKEND_ROOT / "config"), "orderbook_imbalance")
    trade_flow_config = load_signal_config(str(BACKEND_ROOT / "config"), "trade_flow_imbalance")

    coins: list[str] = global_config["assets"]
    lookback_hours = 48
    health_s = int(global_config.get("health_check", {}).get("interval_seconds", 21600))
    db_path = str(BACKEND_ROOT / global_config.get("database", {}).get("path", "data.db"))
    orderbook_snapshot_s = int(orderbook_config.get("snapshot_interval_seconds", 30))
    orderbook_depth_levels = int(orderbook_config.get("depth_levels", 10))
    orderflow_retention_hours = int(trade_flow_config.get("retention_hours", 48))

    signal_hl_client = HLClient()
    signal_bybit_client = BybitClient(
        api_key=os.getenv("BYBIT_API_KEY", ""),
        api_secret=os.getenv("BYBIT_API_SECRET", ""),
    )
    signal_store = SQLiteDataStore(db_path=db_path)
    signal_engine = SignalEngine(
        config_dir=str(BACKEND_ROOT / "config"),
        global_config=global_config,
        store=signal_store,
    )
    alert_manager = AlertManager(
        cooldown_seconds=runtime["cooldown_seconds"],
        cadence=runtime["timeframe"],
    )

    if telegram_enabled:
        signal_telegram = TelegramBot(
            token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            min_interval_seconds=runtime["telegram_min_interval_seconds"],
        )
    else:
        signal_telegram = None

    await signal_hl_client.start()
    await signal_bybit_client.start()

    if signal_telegram:
        await signal_telegram.send_startup_message(
            assets=coins,
            signals=[signal.name for signal in signal_engine._signals],
        )

    signal_stop_event = asyncio.Event()
    signal_refresh_queue: Optional[asyncio.Queue[str]] = None
    if runtime["signal_refresh_enabled"]:
        signal_refresh_queue = asyncio.Queue(maxsize=1000)

    def _enqueue_signal_refresh(coin: str) -> None:
        if signal_refresh_queue is None:
            return
        try:
            signal_refresh_queue.put_nowait(coin)
        except asyncio.QueueFull:
            logger.debug("Signal refresh queue full; dropping update for %s", coin)

    signal_tasks = [
        asyncio.create_task(
            poll_asset_contexts(
                signal_hl_client,
                signal_store,
                coins,
                runtime["context_poll_seconds"],
                signal_stop_event,
                on_update=_enqueue_signal_refresh if signal_refresh_queue else None,
            ),
            name="signal_poll_hl_context",
        ),
        asyncio.create_task(
            poll_funding_history(
                signal_hl_client,
                signal_store,
                coins,
                runtime["funding_poll_seconds"],
                lookback_hours,
                signal_stop_event,
                on_update=_enqueue_signal_refresh if signal_refresh_queue else None,
            ),
            name="signal_poll_hl_funding",
        ),
        asyncio.create_task(
            run_trades_ws(
                signal_hl_client,
                signal_store,
                coins,
                signal_stop_event,
                on_update=_enqueue_signal_refresh if signal_refresh_queue else None,
            ),
            name="signal_ws_trades",
        ),
        asyncio.create_task(
            run_l2book_ws(
                signal_hl_client,
                signal_store,
                coins,
                signal_stop_event,
                snapshot_interval_s=orderbook_snapshot_s,
                depth_levels=orderbook_depth_levels,
                on_update=_enqueue_signal_refresh if signal_refresh_queue else None,
            ),
            name="signal_ws_l2book",
        ),
        asyncio.create_task(
            run_liquidations_ws(
                signal_hl_client,
                signal_store,
                signal_stop_event,
                on_update=_enqueue_signal_refresh if signal_refresh_queue else None,
            ),
            name="signal_ws_liquidations",
        ),
        asyncio.create_task(
            poll_hl_ohlcv(
                signal_hl_client,
                signal_store,
                coins,
                runtime["bybit_ohlcv_seconds"],
                signal_stop_event,
                on_update=_enqueue_signal_refresh if signal_refresh_queue else None,
            ),
            name="signal_poll_hl_ohlcv",
        ),
        asyncio.create_task(
            poll_bybit_oi(
                signal_bybit_client,
                signal_store,
                coins,
                runtime["bybit_oi_seconds"],
                signal_stop_event,
                on_update=_enqueue_signal_refresh if signal_refresh_queue else None,
            ),
            name="signal_poll_bybit_oi",
        ),
        asyncio.create_task(
            poll_bybit_volume(
                signal_bybit_client,
                signal_store,
                coins,
                runtime["bybit_volume_seconds"],
                signal_stop_event,
                on_update=_enqueue_signal_refresh if signal_refresh_queue else None,
            ),
            name="signal_poll_bybit_volume",
        ),
        asyncio.create_task(
            engine_tick_loop(
                signal_engine,
                alert_manager,
                signal_store,
                signal_telegram,
                coins,
                runtime["tick_interval_seconds"],
                signal_stop_event,
                not telegram_enabled,
                runtime["timeframe"],
                _broadcast_signal,
            ),
            name="signal_engine_tick",
        ),
        *(
            [
                asyncio.create_task(
                    signal_refresh_loop(
                        signal_engine,
                        alert_manager,
                        signal_store,
                        signal_telegram,
                        signal_refresh_queue,
                        runtime["signal_refresh_debounce_seconds"],
                        signal_stop_event,
                        not telegram_enabled,
                        runtime["timeframe"],
                        _broadcast_signal,
                    ),
                    name="signal_refresh_loop",
                )
            ]
            if signal_refresh_queue is not None
            else []
        ),
        asyncio.create_task(
            log_data_counts(signal_store, 60, signal_stop_event),
            name="signal_log_counts",
        ),
        asyncio.create_task(
            health_check_loop(
                signal_telegram,
                alert_manager,
                signal_store,
                health_s,
                time.time(),
                signal_stop_event,
                not telegram_enabled,
            ),
            name="signal_health_check",
        ),
        asyncio.create_task(
            prune_ticks_loop(signal_store, orderflow_retention_hours, 3600, signal_stop_event),
            name="signal_prune_orderflow_ticks",
        ),
    ]

    logger.info("Started integrated signal runtime with %s cadence", runtime["timeframe"])


async def stop_signal_runtime() -> None:
    global signal_tasks, signal_stop_event, signal_store
    global signal_hl_client, signal_bybit_client, signal_telegram

    if signal_stop_event:
        signal_stop_event.set()

    for task in signal_tasks:
        task.cancel()

    if signal_tasks:
        await asyncio.gather(*signal_tasks, return_exceptions=True)
    signal_tasks = []

    if signal_hl_client:
        await signal_hl_client.close()
        signal_hl_client = None

    if signal_bybit_client:
        await signal_bybit_client.close()
        signal_bybit_client = None

    if signal_store:
        signal_store.close()
        signal_store = None

    if signal_telegram:
        try:
            await signal_telegram.send_shutdown_message()
        except Exception:
            logger.debug("Skipping telegram shutdown message", exc_info=True)
        signal_telegram = None


@app.get("/")
def read_root():
    return {"status": "ok", "service": "Hypertrade API"}


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "hypertrade-api"}


@app.post("/api/config/interval")
async def update_price_interval(update: IntervalUpdate):
    global PRICE_UPDATE_INTERVAL
    PRICE_UPDATE_INTERVAL = update.interval_seconds
    return {"status": "success", "interval": PRICE_UPDATE_INTERVAL}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    asyncio.create_task(fetch_and_broadcast_prices_once())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
        manager.disconnect(websocket)


@app.websocket("/ws/signals")
async def ws_signals(websocket: WebSocket):
    await signal_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        signal_manager.disconnect(websocket)
    except Exception as exc:
        logger.error("Signal WS error: %s", exc)
        signal_manager.disconnect(websocket)


app.include_router(router, prefix="/api")
app.include_router(signals_router, prefix="/api")
app.include_router(news_router, prefix="/api")
app.include_router(branches_router, prefix="/api")
app.include_router(telegram_router, prefix="/api")


@app.on_event("startup")
async def startup():
    global shared_client, price_task, news_task

    shared_client = HyperliquidClient()
    await shared_client.__aenter__()

    create_db_and_tables()
    watcher.add_callback(broadcast_handler)
    await watcher.start()

    with Session(engine) as session:
        wallets = session.exec(select(Wallet).where(Wallet.is_active == True)).all()
        for wallet in wallets:
            await watcher.subscribe_to_user(wallet.address)

    from core.settings import settings

    if settings.hyperliquid.vault_address:
        await watcher.subscribe_to_user(settings.hyperliquid.vault_address)

    price_task = asyncio.create_task(broadcast_prices_periodically())
    news_task = asyncio.create_task(start_news_polling(interval_minutes=10))
    await start_signal_runtime()


@app.on_event("shutdown")
async def shutdown():
    global shared_client, price_task, news_task

    await watcher.stop()

    for task in (price_task, news_task):
        if task:
            task.cancel()

    await stop_signal_runtime()

    if shared_client:
        await shared_client.__aexit__(None, None, None)
        shared_client = None
