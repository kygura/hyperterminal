from __future__ import annotations

import time

from db.store import SQLiteDataStore
from signals.cvd import CVDDivergenceSignal
from signals.orderbook_imbalance import OrderbookImbalanceSignal
from signals.spot_led_flow import SpotLedFlowSignal
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


def test_store_spot_volume_rolling_avg_uses_spot_rows_only(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "spot_avg.db"))
    now_ms = int(time.time() * 1000)

    store.add_volume_snapshot(
        coin="BTC",
        ts=now_ms - 20_000,
        futures_volume=25_000,
        spot_volume=100_000,
        source="bybit_spot",
    )
    store.add_volume_snapshot(
        coin="BTC",
        ts=now_ms - 10_000,
        futures_volume=40_000,
        spot_volume=300_000,
        source="bybit_spot",
    )
    store.add_trade("BTC", "B", 100_000, 1.0, now_ms - 5_000)

    avg = store.get_spot_volume_rolling_avg("BTC", 60_000)

    assert avg == 200_000
    store.close()


def test_store_add_trade_upserts_same_minute_bucket(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "trade_buckets.db"))
    now_ms = int(time.time() * 1000)

    store.add_trade("BTC", "B", 100_000, 1.0, now_ms)
    store.add_trade("BTC", "B", 100_000, 0.5, now_ms + 5_000)

    rows = store._q(
        "SELECT ts, buy_volume, sell_volume, futures_volume FROM volume_snapshots WHERE asset=? AND source=?",
        ("BTC", "hyperliquid_ws"),
    )

    assert len(rows) == 1
    assert rows[0]["buy_volume"] == 150_000
    assert rows[0]["sell_volume"] == 0
    assert rows[0]["futures_volume"] == 150_000
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


def test_cvd_divergence_signal_uses_raw_trade_ticks(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "cvd_signal.db"))
    base_ts = int(time.time() * 1000) - 12_000

    trades = [
        ("B", 100_000, 1.0),
        ("B", 100_010, 1.0),
        ("B", 100_020, 1.0),
        ("B", 100_030, 1.0),
        ("S", 100_700, 1.0),
        ("S", 100_710, 1.0),
        ("S", 100_720, 1.0),
        ("S", 100_730, 1.0),
    ]
    for idx, (side, px, sz) in enumerate(trades):
        store.add_trade_tick("BTC", side, px, sz, base_ts + idx * 1_000)

    signal = CVDDivergenceSignal(
        name="cvd_divergence",
        config={
            "lookback_minutes": 30,
            "price_change_threshold_pct": 0.5,
            "cvd_reversal_threshold_pct": -20.0,
            "min_trades": 8,
            "thresholds": {"low": 1.0, "medium": 1.5, "high": 2.0},
        },
        store=store,
    )

    result = signal.evaluate("BTC")

    assert result is not None
    assert result.direction == "SHORT_BIAS"
    assert result.metadata["trade_count"] == 8
    store.close()


def test_spot_led_flow_requires_explicit_spot_volume(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "spot_led_missing.db"))
    now_ms = int(time.time() * 1000)

    store.add_trade("BTC", "B", 100_000, 0.5, now_ms - 10_000)
    store.add_trade("BTC", "S", 100_010, 0.4, now_ms - 5_000)
    store.add_snapshot("BTC", funding=0.0, oi=1_000_000, mark_px=100_000, oracle_px=100_000, premium=0.0)
    store.add_snapshot("BTC", funding=0.0, oi=1_000_000, mark_px=100_050, oracle_px=100_050, premium=0.0)

    signal = SpotLedFlowSignal(
        name="spot_led_flow",
        config={
            "spot_surge_threshold_pct": 20.0,
            "oi_flat_band_pct": 3.0,
            "lookback_hours": 24,
            "confirm_readings": 1,
            "thresholds": {"low": 1.0, "medium": 1.5, "high": 2.0},
        },
        store=store,
    )

    result = signal.evaluate("BTC")

    assert result is None
    store.close()


def test_spot_led_flow_triggers_with_explicit_spot_volume(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "spot_led_present.db"))
    now_ms = int(time.time() * 1000)

    store.add_volume_snapshot(
        coin="BTC",
        ts=now_ms - 20 * 60 * 1000,
        futures_volume=20_000,
        spot_volume=100_000,
        source="bybit_spot",
    )
    store.add_volume_snapshot(
        coin="BTC",
        ts=now_ms - 10 * 60 * 1000,
        futures_volume=25_000,
        spot_volume=120_000,
        source="bybit_spot",
    )
    store.add_volume_snapshot(
        coin="BTC",
        ts=now_ms - 2 * 60 * 1000,
        futures_volume=30_000,
        spot_volume=240_000,
        source="bybit_spot",
    )
    store.add_snapshot(
        "BTC",
        funding=0.0,
        oi=1_000_000,
        mark_px=100_000,
        oracle_px=100_000,
        premium=0.0,
    )
    store.add_snapshot(
        "BTC",
        funding=0.0,
        oi=1_000_000,
        mark_px=100_150,
        oracle_px=100_150,
        premium=0.0,
    )

    signal = SpotLedFlowSignal(
        name="spot_led_flow",
        config={
            "spot_surge_threshold_pct": 20.0,
            "oi_flat_band_pct": 3.0,
            "lookback_hours": 24,
            "confirm_readings": 1,
            "thresholds": {"low": 1.0, "medium": 1.5, "high": 2.0},
        },
        store=store,
    )

    result = signal.evaluate("BTC")

    assert result is not None
    assert result.direction == "LONG_BIAS"
    assert result.metadata["spot_volume"] > 0
    store.close()
