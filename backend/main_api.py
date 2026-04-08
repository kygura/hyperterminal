import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select

from api.branches import router as branches_router
from api.news import router as news_router, start_news_polling
from api.routes import router
from api.signals import router as signals_router
from api.telegram import router as telegram_router
from data.hl_client.client import HyperliquidClient
from db.models import Wallet
from db.session import create_db_and_tables, engine
from engine.handler import handle_update
from engine.watcher import watcher
from runtime.signal_runtime import SignalRuntime
from runtime.supervisor import RuntimeSupervisor, TaskSpec

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
        for connection in list(self.active_connections):
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
signal_runtime: Optional[SignalRuntime] = None
api_supervisor: Optional[RuntimeSupervisor] = None
api_stop_event: Optional[asyncio.Event] = None


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
    global signal_runtime
    signal_runtime = SignalRuntime(backend_root=BACKEND_ROOT, dry_run=False, on_candidate=_broadcast_signal)
    await signal_runtime.start()
    logger.info("Started integrated signal runtime with %s cadence", signal_runtime.runtime_settings["timeframe"])


async def stop_signal_runtime() -> None:
    global signal_runtime
    if signal_runtime:
        await signal_runtime.stop()
        signal_runtime = None


@app.get("/")
def read_root():
    return {"status": "ok", "service": "Hypertrade API"}


@app.get("/health")
def health_check():
    signal_snapshot = signal_runtime.snapshot() if signal_runtime else {"status": "stopped"}
    api_snapshot = api_supervisor.snapshot() if api_supervisor else {"status": "stopped"}
    statuses = {signal_snapshot.get("status"), api_snapshot.get("status")}
    overall = "healthy" if statuses <= {"healthy"} else "degraded"
    return {
        "status": overall,
        "service": "hypertrade-api",
        "signal_runtime": signal_snapshot,
        "api_runtime": api_snapshot,
        "watcher": watcher.snapshot(),
    }


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
    global shared_client, api_supervisor, api_stop_event

    try:
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

        api_stop_event = asyncio.Event()
        api_supervisor = RuntimeSupervisor(stop_event=api_stop_event)
        api_supervisor.add(TaskSpec("price_broadcast", broadcast_prices_periodically, critical=False))
        api_supervisor.add(TaskSpec("news_polling", lambda: start_news_polling(interval_minutes=10), critical=False))
        await api_supervisor.start()
        await start_signal_runtime()
    except Exception:
        await shutdown()
        raise


@app.on_event("shutdown")
async def shutdown():
    global shared_client, api_supervisor, api_stop_event

    if api_supervisor:
        await api_supervisor.stop()
        api_supervisor = None
    api_stop_event = None

    await watcher.stop()
    watcher.remove_callback(broadcast_handler)

    await stop_signal_runtime()

    if shared_client:
        await shared_client.__aexit__(None, None, None)
        shared_client = None
