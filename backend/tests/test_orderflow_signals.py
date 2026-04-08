from __future__ import annotations

import time

from db.store import SQLiteDataStore
from signals.orderbook_imbalance import OrderbookImbalanceSignal
from signals.trade_flow_imbalance import TradeFlowImbalanceSignal


def _book_levels(px: float, size: float) -> list[dict]:
    return [
        {"px": px, "sz": size, "n": 2},
        {"px": px - 1, "sz": size * 0.5, "n": 1},
    ]


def test_store_persists_trade_ticks_and_orderbook_snapshots(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "orderflow.db"))
    now_ms = int(time.time() * 1000)

    store.add_trade("BTC", "B", 100_000, 0.75, now_ms)
    store.add_orderbook_snapshot(
        coin="BTC",
        ts=now_ms,
        bids=_book_levels(100_000, 5),
        asks=[
            {"px": 100_001, "sz": 3, "n": 2},
            {"px": 100_002, "sz": 2, "n": 1},
        ],
        depth_levels=2,
    )

    ticks = store.get_trade_ticks("BTC", 60_000)
    book = store.get_orderbook_imbalance_window("BTC", 60_000)

    assert len(ticks) == 1
    assert ticks[0]["notional"] == 75_000
    assert len(book) == 1
    assert book[0]["bid_total"] > book[0]["ask_total"]
    store.close()


def test_orderbook_imbalance_signal_detects_persistent_bid_pressure(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "orderbook_signal.db"))
    base_ts = int(time.time() * 1000) - 10_000

    for idx in range(3):
        ts = base_ts + idx * 1_000
        store.add_orderbook_snapshot(
            coin="BTC",
            ts=ts,
            bids=[{"px": 100_000, "sz": 5, "n": 2}],
            asks=[{"px": 100_001, "sz": 5, "n": 2}],
            depth_levels=1,
        )

    for idx in range(3, 6):
        ts = base_ts + idx * 1_000
        store.add_orderbook_snapshot(
            coin="BTC",
            ts=ts,
            bids=[{"px": 100_000, "sz": 9, "n": 3}],
            asks=[{"px": 100_001, "sz": 2, "n": 1}],
            depth_levels=1,
        )

    signal = OrderbookImbalanceSignal(
        name="orderbook_imbalance",
        config={
            "lookback_minutes": 60,
            "min_snapshots": 6,
            "persistence_count": 3,
            "imbalance_threshold": 0.1,
            "thresholds": {"low": 1.0, "medium": 1.5, "high": 2.0},
        },
        store=store,
    )
    signal.global_config = {"strategy": {"timeframe": "hourly"}}

    result = signal.evaluate("BTC")

    assert result is not None
    assert result.direction == "LONG_BIAS"
    assert result.metadata["persistence_count"] == 3
    store.close()


def test_trade_flow_imbalance_signal_detects_whale_buy_skew(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "trade_flow_signal.db"))
    now_ms = int(time.time() * 1000)

    for idx in range(12):
        ts = now_ms - (12 - idx) * 1_000
        side = "B" if idx < 10 else "S"
        price = 100_000 + idx
        size = 0.6 if side == "B" else 0.2
        store.add_trade_tick("BTC", side, price, size, ts)

    signal = TradeFlowImbalanceSignal(
        name="trade_flow_imbalance",
        config={
            "lookback_minutes": 60,
            "min_trades": 10,
            "delta_z_threshold": 10.0,
            "whale_threshold_usd": 50_000,
            "whale_skew_threshold": 0.65,
            "absorption_price_tolerance_pct": 0.01,
            "thresholds": {"low": 1.0, "medium": 1.5, "high": 2.0},
        },
        store=store,
    )
    signal.global_config = {"strategy": {"timeframe": "hourly"}}

    result = signal.evaluate("BTC")

    assert result is not None
    assert result.direction == "LONG_BIAS"
    assert result.metadata["sub_signal"] == "whale_skew"
    store.close()
