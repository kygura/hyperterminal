"""
Bybit REST API client — supplemental data source for OHLCV, OI, and volume.

Uses Bybit V5 API (unified endpoint). API key is optional for public endpoints
but included for rate-limit benefits.

Docs: https://bybit-exchange.github.io/docs/v5/intro
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import random
import time
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.bybit.com"


class BybitClient:
    """Async client for Bybit V5 public (and authenticated) REST endpoints."""

    def __init__(self, api_key: str = "", api_secret: str = "") -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_read=10),
        )
        logger.info("BybitClient started")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("BybitClient closed")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _sign(self, params: dict, ts: int) -> str:
        """HMAC-SHA256 signature for authenticated requests."""
        param_str = str(ts) + self._api_key + "5000" + urlencode(params)
        return hmac.new(
            self._api_secret.encode("utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _get(
        self,
        path: str,
        params: dict,
        auth: bool = False,
    ) -> Optional[Any]:
        if self._session is None or self._session.closed:
            logger.error("BybitClient: session not started")
            return None
        url = _BASE_URL + path
        headers = {}
        if auth and self._api_key:
            ts = int(time.time() * 1000)
            params = dict(params)
            sig = self._sign(params, ts)
            headers = {
                "X-BAPI-API-KEY": self._api_key,
                "X-BAPI-TIMESTAMP": str(ts),
                "X-BAPI-RECV-WINDOW": "5000",
                "X-BAPI-SIGN": sig,
            }
        retryable_statuses = {408, 429, 500, 502, 503, 504}
        for attempt in range(1, 4):
            try:
                async with self._session.get(url, params=params, headers=headers) as resp:
                    if resp.status in retryable_statuses and attempt < 3:
                        retry_after = resp.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after else (0.5 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.25))
                        logger.warning("BybitClient retryable HTTP %s for %s (attempt %d/3)", resp.status, path, attempt)
                        await asyncio.sleep(delay)
                        continue
                    resp.raise_for_status()
                    data = await resp.json()
                    if data.get("retCode", 0) != 0:
                        logger.warning(
                            "Bybit API error %s: %s (path=%s params=%s)",
                            data.get("retCode"), data.get("retMsg"), path, params,
                        )
                        return None
                    return data.get("result")
            except aiohttp.ClientResponseError as exc:
                if exc.status in retryable_statuses and attempt < 3:
                    delay = 0.5 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.25)
                    logger.warning("BybitClient retryable HTTP %s for %s (attempt %d/3)", exc.status, path, attempt)
                    await asyncio.sleep(delay)
                    continue
                logger.error("BybitClient HTTP %s for %s: %s", exc.status, path, exc)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < 3:
                    delay = 0.5 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.25)
                    logger.warning("BybitClient transient error for %s (attempt %d/3): %s", path, attempt, exc)
                    await asyncio.sleep(delay)
                    continue
                logger.error("BybitClient network error for %s: %s", path, exc)
            except Exception as exc:
                logger.error("BybitClient unexpected error for %s: %s", path, exc, exc_info=True)
                break
        return None

    # ------------------------------------------------------------------
    # OHLCV — Linear (USDT perpetual) klines
    # ------------------------------------------------------------------

    async def get_klines(
        self,
        symbol: str,
        interval: str = "60",   # "60" = 1h in Bybit V5
        limit: int = 48,
    ) -> list[dict]:
        """
        Returns list of OHLCV candles, oldest first.
        Each: {ts, open, high, low, close, volume}

        symbol: e.g. "BTCUSDT"
        interval: Bybit interval string ("1","3","5","15","30","60","120","240","D","W","M")
        """
        result = await self._get(
            "/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
        )
        if not result or "list" not in result:
            return []
        candles = []
        for row in reversed(result["list"]):
            # row: [startTime, open, high, low, close, volume, turnover]
            try:
                candles.append({
                    "ts": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                })
            except (IndexError, ValueError, TypeError):
                continue
        logger.debug("BybitClient: got %d klines for %s", len(candles), symbol)
        return candles

    # ------------------------------------------------------------------
    # Open Interest
    # ------------------------------------------------------------------

    async def get_open_interest(
        self,
        symbol: str,
        interval_time: str = "1h",
        limit: int = 48,
    ) -> list[dict]:
        """
        Returns list of OI readings: {ts, oi}

        interval_time: "5min","15min","30min","1h","4h","1d"
        """
        result = await self._get(
            "/v5/market/open-interest",
            {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": interval_time,
                "limit": limit,
            },
        )
        if not result or "list" not in result:
            return []
        readings = []
        for row in reversed(result["list"]):
            try:
                readings.append({
                    "ts": int(row["timestamp"]),
                    "oi": float(row["openInterest"]),
                })
            except (KeyError, ValueError, TypeError):
                continue
        logger.debug("BybitClient: got %d OI readings for %s", len(readings), symbol)
        return readings

    # ------------------------------------------------------------------
    # Spot volume (from spot klines)
    # ------------------------------------------------------------------

    async def get_spot_volume(
        self,
        symbol: str,
        interval: str = "60",
        limit: int = 48,
    ) -> list[dict]:
        """
        Spot klines from Bybit spot market.
        symbol: e.g. "BTCUSDT" (spot category)
        Returns: [{ts, volume}]
        """
        result = await self._get(
            "/v5/market/kline",
            {"category": "spot", "symbol": symbol, "interval": interval, "limit": limit},
        )
        if not result or "list" not in result:
            return []
        candles = []
        for row in reversed(result["list"]):
            try:
                candles.append({
                    "ts": int(row[0]),
                    "volume": float(row[5]),  # base volume
                    "turnover": float(row[6]),  # quote turnover (USD-ish)
                })
            except (IndexError, ValueError, TypeError):
                continue
        return candles

    # ------------------------------------------------------------------
    # Funding rate history
    # ------------------------------------------------------------------

    async def get_funding_history(
        self,
        symbol: str,
        limit: int = 20,
    ) -> list[dict]:
        """
        Returns recent funding rate history.
        Each: {ts, rate}
        """
        result = await self._get(
            "/v5/market/funding/history",
            {"category": "linear", "symbol": symbol, "limit": limit},
        )
        if not result or "list" not in result:
            return []
        entries = []
        for row in result["list"]:
            try:
                entries.append({
                    "ts": int(row["fundingRateTimestamp"]),
                    "rate": float(row["fundingRate"]),
                })
            except (KeyError, ValueError, TypeError):
                continue
        return list(reversed(entries))  # oldest first

    # ------------------------------------------------------------------
    # Symbol mapping helper
    # ------------------------------------------------------------------

    @staticmethod
    def asset_to_symbol(asset: str, category: str = "linear") -> str:
        """Convert asset name (e.g. 'BTC') to Bybit symbol (e.g. 'BTCUSDT')."""
        return f"{asset}USDT"
