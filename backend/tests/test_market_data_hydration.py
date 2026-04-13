from __future__ import annotations

import asyncio
import time

from db.store import SQLiteDataStore
from main_daemon import poll_bybit_ohlcv


class StubBybitClient:
    async def get_klines(self, symbol: str, interval: str = "60", limit: int = 50) -> list[dict]:
        assert symbol == "BTCUSDT"
        assert interval == "60"
        return [
            {
                "ts": int(time.time() * 1000),
                "open": 62000.0,
                "high": 62500.0,
                "low": 61800.0,
                "close": 62400.0,
                "volume": 1234.5,
            }
        ]


def test_poll_bybit_ohlcv_hydrates_store(tmp_path):
    store = SQLiteDataStore(str(tmp_path / "hydration.db"))
    stop_event = asyncio.Event()
    updates: list[str] = []

    def on_update(asset: str) -> None:
        updates.append(asset)
        stop_event.set()

    asyncio.run(
        poll_bybit_ohlcv(
            StubBybitClient(),
            store,
            ["BTC"],
            interval_s=3600,
            stop_event=stop_event,
            on_update=on_update,
        )
    )

    candles = store.get_ohlcv_window("BTC", lookback_ms=365 * 24 * 3600 * 1000)
    assert len(candles) == 1
    assert candles[0]["close"] == 62400.0
    assert candles[0]["volume"] == 1234.5
    assert updates == ["BTC"]

    store.close()
