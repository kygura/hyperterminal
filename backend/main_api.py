from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from db.session import create_db_and_tables, engine
from db.models import Wallet
from api.routes import router
from api.signals import router as signals_router
from api.news import router as news_router, start_news_polling
from api.branches import router as branches_router
from api.telegram import router as telegram_router
from engine.watcher import watcher
from engine.handler import handle_update
from sqlmodel import Session, select
from data.hl_client.client import HyperliquidClient
from typing import Optional
from pydantic import BaseModel
import asyncio
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store active websocket connections
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
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting: {e}")

manager = ConnectionManager()

# Shared client instance for the app lifecycle
shared_client: Optional[HyperliquidClient] = None

# Wrapper for handler to also broadcast to UI
async def broadcast_handler(address: str, data: dict):
    # Persist to DB
    await handle_update(address, data)
    # Broadcast to UI
    await manager.broadcast({"address": address, "data": data})

# Background task to broadcast prices every 15 minutes
# Background task to broadcast prices every 15 minutes
async def broadcast_prices_periodically():
    """Fetch and broadcast asset contexts from Hyperliquid."""
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
                
                # Map symbol -> full context (canonical structure)
                for i, ctx in enumerate(asset_ctxs):
                    if i < len(universe) and universe[i]["name"] in assets:
                        symbol = universe[i]["name"]
                        # Send the FULL context object as-is (matches PerpAssetCtx)
                        prices_data[symbol] = ctx
            
            # Broadcast to all connected clients
            if prices_data:
                await manager.broadcast({
                    "type": "prices",
                    "data": prices_data,
                    "timestamp": asyncio.get_event_loop().time()
                })
                logger.info(f"Broadcasting prices: {list(prices_data.keys())}")
            
        except Exception as e:
            logger.error(f"Error fetching/broadcasting prices: {e}")
        
        await asyncio.sleep(PRICE_UPDATE_INTERVAL)

async def fetch_and_broadcast_prices_once():
    """Fetch and broadcast prices once (for initial connection)"""
    global shared_client
    if not shared_client:
        return
    
    assets = ["BTC", "ETH", "SOL", "HYPE"]
    
    try:
        meta, asset_ctxs = await shared_client.get_meta_and_asset_ctxs()
        prices_data = {}
        
        if meta and asset_ctxs:
            universe = meta.get("universe", [])
            for i, ctx in enumerate(asset_ctxs):
                if i < len(universe) and universe[i]["name"] in assets:
                    symbol = universe[i]["name"]
                    # Send full context (canonical structure)
                    prices_data[symbol] = ctx
        
        if prices_data:
            await manager.broadcast({
                "type": "prices",
                "data": prices_data,
                "timestamp": asyncio.get_event_loop().time()
            })
            logger.info(f"Initial price broadcast: {list(prices_data.keys())}")
    except Exception as e:
        logger.error(f"Failed to fetch initial prices: {e}")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    
    # Send prices immediately when first client connects
    if len(manager.active_connections) == 1:
        asyncio.create_task(fetch_and_broadcast_prices_once())
    
    try:
        while True:
            # Keep alive and listen for any client messages if needed
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# Global interval state
PRICE_UPDATE_INTERVAL = int(os.getenv("PRICE_UPDATE_INTERVAL_SECONDS", "300"))

class IntervalUpdate(BaseModel):
    interval_seconds: int

@app.post("/api/config/interval")
async def update_price_interval(update: IntervalUpdate):
    global PRICE_UPDATE_INTERVAL
    PRICE_UPDATE_INTERVAL = update.interval_seconds
    logger.info(f"Updated price polling interval to {PRICE_UPDATE_INTERVAL} seconds")
    return {"status": "success", "interval": PRICE_UPDATE_INTERVAL}

_last_signal_ts: str = ""

async def _broadcast_signals_periodically():
    """Poll the signal daemon's SQLite for new trade candidates and broadcast them."""
    global _last_signal_ts
    import sqlite3
    from pathlib import Path

    db_candidates = [
        Path(os.getenv("SIGNAL_DB_PATH", "")),
        Path(__file__).parent / "data.db",
        Path("/app/data/data.db"),
    ]

    while True:
        await asyncio.sleep(30)
        db_path = next((p for p in db_candidates if p and p.exists()), None)
        if not db_path:
            continue
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if _last_signal_ts:
                cur.execute(
                    "SELECT * FROM trade_candidates WHERE ts > ? ORDER BY ts ASC LIMIT 20",
                    (_last_signal_ts,),
                )
            else:
                cur.execute(
                    "SELECT * FROM trade_candidates ORDER BY ts DESC LIMIT 5"
                )
            rows = cur.fetchall()
            conn.close()

            for row in rows:
                d = dict(row)
                import json
                if isinstance(d.get("signals_json"), str):
                    try:
                        d["signals_json"] = json.loads(d["signals_json"])
                    except Exception:
                        pass
                _last_signal_ts = d.get("ts", _last_signal_ts)
                await manager.broadcast({"type": "signal", "data": d})
        except Exception as e:
            logger.debug(f"Signal poll: {e}")


@app.get("/")
def read_root():
    return {"status": "ok", "service": "Hyperliquid Copy Trader"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "hypertrade-api"}

# /ws/signals — frontend connects here for live signal updates
@app.websocket("/ws/signals")
async def ws_signals(websocket: WebSocket):
    await manager.connect(websocket)
    # Send initial prices on connect
    asyncio.create_task(fetch_and_broadcast_prices_once())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Signal WS error: {e}")
        manager.disconnect(websocket)

app.include_router(router, prefix="/api")
app.include_router(signals_router, prefix="/api")
app.include_router(news_router, prefix="/api")
app.include_router(branches_router, prefix="/api")
app.include_router(telegram_router, prefix="/api")

@app.on_event("startup")
async def startup():
    global shared_client
    
    # Initialize shared HTTP client
    shared_client = HyperliquidClient()
    # Initialize the session
    await shared_client.__aenter__()
    logger.info("Initialized shared HyperliquidClient")
    
    create_db_and_tables()
    watcher.add_callback(broadcast_handler)
    await watcher.start()
    
    # Load existing wallets and subscribe
    with Session(engine) as session:
        wallets = session.exec(select(Wallet).where(Wallet.is_active == True)).all()
        for w in wallets:
            logger.info(f"Subscribing to existing wallet: {w.address}")
            await watcher.subscribe_to_user(w.address)
            
    # Subscribe to own vault wallet if configured
    from core.settings import settings
    if settings.hyperliquid.vault_address:
        logger.info(f"Subscribing to vault wallet: {settings.hyperliquid.vault_address}")
        await watcher.subscribe_to_user(settings.hyperliquid.vault_address)
    
    # Start price broadcasting task
    asyncio.create_task(broadcast_prices_periodically())
    logger.info("Started periodic price broadcasting task")

    # Start news polling task (every 10 minutes)
    asyncio.create_task(start_news_polling(interval_minutes=10))
    logger.info("Started news polling task")

    # Start signal broadcaster (polls daemon DB every 30s for new signals)
    asyncio.create_task(_broadcast_signals_periodically())
    logger.info("Started signal broadcasting task")

@app.on_event("shutdown")
async def shutdown():
    global shared_client
    
    await watcher.stop()
    
    # Close shared HTTP client
    if shared_client:
        await shared_client.__aexit__(None, None, None)
        logger.info("Closed shared HyperliquidClient")
