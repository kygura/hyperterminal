"""
Hyperliquid async API client.

All requests go to https://api.hyperliquid.xyz/info (POST, JSON).
A single aiohttp.ClientSession is used for the lifetime of the client.
WebSocket connections target wss://api.hyperliquid.xyz/ws.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Any, Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

_BASE_HTTP = "https://api.hyperliquid.xyz/info"
_BASE_WS = "wss://api.hyperliquid.xyz/ws"
_HEADERS = {"Content-Type": "application/json"}


class HLClient:
    """Async client for Hyperliquid public API."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open the shared HTTP session."""
        connector = aiohttp.TCPConnector(ssl=True)
        self._session = aiohttp.ClientSession(
            connector=connector,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=30),
        )
        logger.info("HLClient HTTP session started")

    async def close(self) -> None:
        """Close the shared HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("HLClient HTTP session closed")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post(self, payload: dict) -> Optional[Any]:
        if self._session is None or self._session.closed:
            logger.error("HLClient: session not started or already closed")
            return None
        try:
            async with self._session.post(_BASE_HTTP, json=payload) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as exc:
            logger.error("HLClient HTTP %s for payload %s: %s", exc.status, payload.get("type"), exc)
            return None
        except aiohttp.ClientError as exc:
            logger.error("HLClient network error for payload %s: %s", payload.get("type"), exc)
            return None
        except Exception as exc:
            logger.error("HLClient unexpected error for payload %s: %s", payload.get("type"), exc, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # REST methods
    # ------------------------------------------------------------------

    async def get_asset_contexts(self) -> Optional[dict[str, dict]]:
        """
        POST {type: metaAndAssetCtxs}.
        Returns dict[coin_name -> {fundingRate, openInterest, markPx, oraclePx, premium}].
        """
        try:
            data = await self._post({"type": "metaAndAssetCtxs"})
            if data is None:
                return None
            meta, contexts = data[0], data[1]
            result: dict[str, dict] = {}
            for i, asset_info in enumerate(meta.get("universe", [])):
                name = asset_info.get("name")
                if name is None or i >= len(contexts):
                    continue
                ctx = contexts[i]
                try:
                    result[name] = {
                        "fundingRate": float(ctx.get("funding", 0) or 0),
                        "openInterest": float(ctx.get("openInterest", 0) or 0),
                        "markPx": float(ctx.get("markPx", 0) or 0),
                        "oraclePx": float(ctx.get("oraclePx", 0) or 0),
                        "premium": float(ctx.get("premium", 0) or 0) if ctx.get("premium") is not None else 0.0,
                    }
                except (TypeError, ValueError) as exc:
                    logger.debug("HLClient: failed to parse ctx for %s: %s", name, exc)
                    continue
            logger.debug("HLClient: got asset contexts for %d assets", len(result))
            return result
        except Exception as exc:
            logger.error("HLClient.get_asset_contexts unexpected error: %s", exc, exc_info=True)
            return None

    async def get_funding_history(
        self,
        coin: str,
        start_time: int,
        end_time: Optional[int] = None,
    ) -> Optional[list[dict]]:
        """
        POST {type: fundingHistory, coin, startTime[, endTime]}.
        Returns list of {coin, fundingRate, premium, time}.
        """
        try:
            payload: dict = {"type": "fundingHistory", "coin": coin, "startTime": start_time}
            if end_time is not None:
                payload["endTime"] = end_time
            data = await self._post(payload)
            if data is None:
                return None
            parsed: list[dict] = []
            for entry in data:
                try:
                    parsed.append({
                        "coin": entry.get("coin", coin),
                        "fundingRate": float(entry.get("fundingRate", 0) or 0),
                        "premium": float(entry.get("premium", 0) or 0) if entry.get("premium") is not None else 0.0,
                        "time": int(entry.get("time", 0)),
                    })
                except (TypeError, ValueError) as exc:
                    logger.debug("HLClient: skipping funding entry %s: %s", entry, exc)
                    continue
            logger.debug("HLClient: got %d funding history entries for %s", len(parsed), coin)
            return parsed
        except Exception as exc:
            logger.error("HLClient.get_funding_history unexpected error for %s: %s", coin, exc, exc_info=True)
            return None

    async def get_candle_snapshot(
        self,
        coin: str,
        interval: str,
        start_time: int,
        end_time: Optional[int] = None,
    ) -> Optional[list[dict]]:
        """
        POST {type: candleSnapshot, req: {coin, interval, startTime[, endTime]}}.
        Intervals: "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w", "1M"
        Returns list of {ts, open, high, low, close, volume}.
        """
        try:
            req = {"coin": coin, "interval": interval, "startTime": start_time}
            if end_time is not None:
                req["endTime"] = end_time
            payload = {"type": "candleSnapshot", "req": req}
            
            data = await self._post(payload)
            if data is None:
                return None
                
            parsed: list[dict] = []
            for c in data:
                try:
                    parsed.append({
                        "ts": int(c.get("t", 0)),
                        "open": float(c.get("o", 0)),
                        "high": float(c.get("h", 0)),
                        "low": float(c.get("l", 0)),
                        "close": float(c.get("c", 0)),
                        "volume": float(c.get("v", 0)),  # this is base volume usually
                    })
                except (TypeError, ValueError) as exc:
                    logger.debug("HLClient: skipping candle %s: %s", c, exc)
                    continue
            logger.debug("HLClient: got %d candles for %s", len(parsed), coin)
            return parsed
        except Exception as exc:
            logger.error("HLClient.get_candle_snapshot unexpected error for %s: %s", coin, exc, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------

    async def _ws_connect_with_backoff(
        self,
        name: str,
        subscribe_msgs: list[dict],
        message_handler: Callable[[dict], None],
        stop_event: asyncio.Event,
    ) -> None:
        """
        Maintain a WebSocket connection with exponential backoff reconnection.
        Calls message_handler for every non-ping message received.
        Respects stop_event for graceful shutdown.
        After 10 consecutive failures, logs ERROR and waits max backoff before retrying.
        """
        backoff = 1.0
        max_backoff = 60.0
        consecutive_failures = 0

        while not stop_event.is_set():
            try:
                logger.info("HLClient[%s]: connecting to WebSocket", name)
                async with aiohttp.ClientSession() as ws_session:
                    async with ws_session.ws_connect(_BASE_WS, heartbeat=30) as ws:
                        logger.info("HLClient[%s]: WebSocket connected", name)
                        backoff = 1.0
                        consecutive_failures = 0

                        # Send subscriptions
                        for msg in subscribe_msgs:
                            await ws.send_json(msg)

                        async for raw_msg in ws:
                            if stop_event.is_set():
                                break
                            if raw_msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(raw_msg.data)
                                    message_handler(data)
                                except json.JSONDecodeError as exc:
                                    logger.debug("HLClient[%s]: JSON parse error: %s", name, exc)
                                except Exception as exc:
                                    logger.warning("HLClient[%s]: handler error: %s", name, exc, exc_info=True)
                            elif raw_msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                                logger.warning("HLClient[%s]: WS closed/errored: %s", name, raw_msg)
                                break

            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                consecutive_failures += 1
                logger.warning(
                    "HLClient[%s]: WS disconnected (#%d): %s",
                    name, consecutive_failures, exc,
                )
                if consecutive_failures >= 10:
                    logger.error(
                        "HLClient[%s]: 10 consecutive WS failures. Backing off %ds.",
                        name, int(max_backoff),
                    )
            except Exception as exc:
                consecutive_failures += 1
                logger.error("HLClient[%s]: unexpected WS error: %s", name, exc, exc_info=True)

            if stop_event.is_set():
                break

            logger.info("HLClient[%s]: reconnecting in %.1fs", name, backoff)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass

            backoff = min(backoff * 2, max_backoff)

        logger.info("HLClient[%s]: WebSocket task stopped", name)

    async def connect_trades_ws(
        self,
        coins: list[str],
        callback: Callable[[str, str, float, float, int], None],
        stop_event: asyncio.Event,
    ) -> None:
        """
        Subscribe to trades channel for given coins.
        Calls callback(coin, side, px, sz, time) for each trade.
        """
        subscribe_msgs = [
            {"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}
            for coin in coins
        ]

        def handler(msg: dict) -> None:
            channel = msg.get("channel")
            data = msg.get("data")
            if channel != "trades" or not isinstance(data, list):
                return
            for trade in data:
                try:
                    coin = trade.get("coin", "")
                    side = trade.get("side", "")
                    px = float(trade.get("px", 0))
                    sz = float(trade.get("sz", 0))
                    ts = int(trade.get("time", 0))
                    if coin and side and not math.isnan(px) and not math.isnan(sz):
                        callback(coin, side, px, sz, ts)
                        logger.debug("Trade: %s %s px=%.4f sz=%.4f", coin, side, px, sz)
                except (TypeError, ValueError, KeyError) as exc:
                    logger.debug("HLClient: trade parse error: %s", exc)

        await self._ws_connect_with_backoff("trades", subscribe_msgs, handler, stop_event)

    async def connect_liquidations_ws(
        self,
        callback: Callable[[str, str, float, float, int], None],
        stop_event: asyncio.Event,
    ) -> None:
        """
        Subscribe to liquidations channel.
        Calls callback(coin, side, px, sz, time) for each liquidation.
        """
        subscribe_msgs = [{"method": "subscribe", "subscription": {"type": "liquidations"}}]

        def handler(msg: dict) -> None:
            channel = msg.get("channel")
            data = msg.get("data")
            if channel != "liquidations" or not isinstance(data, dict):
                return
            try:
                coin = data.get("coin", "")
                side = "B" if data.get("isLiquidated", False) else "S"
                px = float(data.get("px", 0))
                sz = float(data.get("sz", 0))
                ts = int(data.get("time", 0))
                if coin and not math.isnan(px) and not math.isnan(sz):
                    callback(coin, side, px, sz, ts)
                    logger.debug("Liquidation: %s %s px=%.4f sz=%.4f", coin, side, px, sz)
            except (TypeError, ValueError, KeyError) as exc:
                logger.debug("HLClient: liquidation parse error: %s", exc)

        await self._ws_connect_with_backoff("liquidations", subscribe_msgs, handler, stop_event)

    async def connect_l2book_ws(
        self,
        coins: list[str],
        callback: Callable[[str, list[dict], list[dict], int], None],
        stop_event: asyncio.Event,
    ) -> None:
        """
        Subscribe to L2 book channel for given coins.
        Calls callback(coin, bids, asks, time) for each snapshot update.
        """
        subscribe_msgs = [
            {"method": "subscribe", "subscription": {"type": "l2Book", "coin": coin}}
            for coin in coins
        ]

        def handler(msg: dict) -> None:
            channel = msg.get("channel")
            data = msg.get("data")
            if channel != "l2Book" or not isinstance(data, dict):
                return
            levels = data.get("levels")
            if not isinstance(levels, list) or len(levels) != 2:
                return
            bids, asks = levels
            if not isinstance(bids, list) or not isinstance(asks, list):
                return
            try:
                coin = data.get("coin", "")
                ts = int(data.get("time", 0))
                if coin and ts > 0:
                    callback(coin, bids, asks, ts)
                    logger.debug(
                        "L2 book: %s bids=%d asks=%d ts=%d",
                        coin,
                        len(bids),
                        len(asks),
                        ts,
                    )
            except (TypeError, ValueError, KeyError) as exc:
                logger.debug("HLClient: l2Book parse error: %s", exc)

        await self._ws_connect_with_backoff("l2Book", subscribe_msgs, handler, stop_event)
