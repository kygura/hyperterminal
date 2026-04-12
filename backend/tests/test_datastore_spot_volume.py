import math
import time

from db.store import SQLiteDataStore


def test_spot_volume_rolling_avg_uses_spot(tmp_path):
    """Ensure SpotLedFlow averages Bybit spot buckets, not futures volume."""
    db_path = tmp_path / "datastore.db"
    store = SQLiteDataStore(str(db_path))
    try:
        now = int(time.time() * 1000)
        store.add_volume_snapshot("BTC", now, futures_volume=2000.0, spot_volume=120.0)
        store.add_volume_snapshot(
            "BTC",
            now + 30_000,
            futures_volume=9000.0,
            spot_volume=0.0,
            source="hyperliquid_ws",
        )
        store.add_volume_snapshot("BTC", now + 60_000, futures_volume=3000.0, spot_volume=180.0)
        lookback_ms = 10 * 60_000
        avg = store.get_spot_volume_rolling_avg("BTC", lookback_ms)
        assert math.isclose(avg, 150.0, rel_tol=1e-6)
    finally:
        store.close()
