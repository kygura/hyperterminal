from __future__ import annotations

import sqlite3

from db.store import SQLiteDataStore


def test_store_rebuilds_db_when_unique_indexes_cannot_be_applied(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE funding_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            asset TEXT NOT NULL,
            source TEXT NOT NULL,
            rate REAL NOT NULL,
            predicted REAL
        );
        INSERT INTO funding_rates(ts, asset, source, rate, predicted) VALUES (1, 'BTC', 'hyperliquid', 0.1, 0.0);
        INSERT INTO funding_rates(ts, asset, source, rate, predicted) VALUES (1, 'BTC', 'hyperliquid', 0.1, 0.0);
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteDataStore(db_path=str(db_path))

    assert store.counts()["funding_rates"] == 0
    store.add_funding("BTC", 0.1, 0.0, 1, source="hyperliquid")
    assert store.counts()["funding_rates"] == 1
    store.close()
