import time

from db.store import SQLiteDataStore


def test_spot_volume_rolling_avg_ignores_futures_only_buckets(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "spot_volume_avg.db"))
    now_ms = int(time.time() * 1000)

    store.add_volume_snapshot(
        coin="BTC",
        ts=now_ms - 30 * 60 * 1000,
        futures_volume=20_000,
        spot_volume=100.0,
        source="bybit_spot",
    )
    store.add_volume_snapshot(
        coin="BTC",
        ts=now_ms - 20 * 60 * 1000,
        futures_volume=55_000,
        spot_volume=0.0,
        source="hyperliquid_ws",
    )
    store.add_volume_snapshot(
        coin="BTC",
        ts=now_ms - 10 * 60 * 1000,
        futures_volume=25_000,
        spot_volume=200.0,
        source="bybit_spot",
    )

    avg = store.get_spot_volume_rolling_avg("BTC", 60 * 60 * 1000)

    assert avg == 150.0
