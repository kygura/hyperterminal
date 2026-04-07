import asyncio
import logging
import json
from typing import Optional, List, Dict, Callable
from data.hl_client.websocket import HyperliquidWebSocket

logger = logging.getLogger(__name__)

class Watcher:
    def __init__(self, ws_url="wss://api.hyperliquid.xyz/ws"):
        self.ws_url = ws_url
        self.ws_client = HyperliquidWebSocket(ws_url)
        self.callbacks = []
        self._running = False
        self._listen_task = None
        
    def add_callback(self, callback: Callable[[str, Dict], None]):
        """Callback signature: (address: str, data: dict) -> None"""
        self.callbacks.append(callback)

    async def start(self):
        if self._running:
            return
        self._running = True
        self._listen_task = asyncio.create_task(self.ws_client.run())
        logger.info("Watcher started")

    async def stop(self):
        self._running = False
        try:
            await self.ws_client.stop()
        except AttributeError:
            # Suppress "ClientConnection object has no attribute 'closed'" error from library
            pass
        except Exception as e:
            logger.error(f"Error stopping watcher: {e}")
            
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        logger.info("Watcher stopped")

    async def subscribe_to_user(self, address: str):
        async def user_callback(update):
            for cb in self.callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(address, update.data)
                    else:
                        cb(address, update.data)
                except Exception as e:
                    logger.error(f"Error in callback for {address}: {e}")

        await self.ws_client.subscribe_user(address, user_callback)
        logger.info(f"Subscribed to user updates for {address}")

# Create a global instance for easy import
watcher = Watcher()
