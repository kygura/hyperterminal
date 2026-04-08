from __future__ import annotations

import time
from pathlib import Path

from db.store import SQLiteDataStore
from engine.signal_engine import SignalEngine
from signals.funding_velocity import FundingVelocitySignal
from signals.leverage_flush import LeverageFlushSignal
from signals.liquidation_cascade import LiquidationCascadeSignal


def test_store_persists_liquidations_and_summary(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "liquidations.db"))
    now_ms = int(time.time() * 1000)

    store.add_liquidation("BTC", "B", 100_000, 0.5, now_ms - 2_000)
    store.add_liquidation("BTC", "S", 100_200, 0.25, now_ms - 1_000)

    liquidations = store.get_liquidations_window("BTC", 60_000)
    summary = store.get_liquidation_summary("BTC", 60_000)

    assert len(liquidations) == 2
    assert summary["long_count"] == 1
    assert summary["short_count"] == 1
    assert summary["total_notional"] == liquidations[0]["notional"] + liquidations[1]["notional"]
    store.close()


def test_liquidation_cascade_signal_detects_long_liq_continuation(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "cascade.db"))
    now_ms = int(time.time() * 1000)
    bucket_ms = 10 * 60 * 1000
    base_ts = now_ms - 55 * 60 * 1000

    historical_sizes = [0.10, 0.18, 0.12, 0.16, 0.14]
    for idx, size in enumerate(historical_sizes):
        ts = base_ts + idx * bucket_ms + 1_000
        store.add_liquidation("BTC", "B", 100_000, size, ts)
        store.add_liquidation("BTC", "S", 100_000, size * 0.2, ts + 1_000)

    current_bucket_ts = base_ts + 5 * bucket_ms + 1_000
    for idx in range(6):
        store.add_liquidation("BTC", "B", 100_000, 1.0, current_bucket_ts + idx * 30_000)

    signal = LiquidationCascadeSignal(
        name="liquidation_cascade",
        config={
            "lookback_minutes": 10,
            "baseline_lookback_minutes": 60,
            "min_events": 6,
            "intensity_z_threshold": 1.0,
            "dominance_threshold": 0.6,
            "acceleration_threshold": 1.1,
            "exhaustion_z_threshold": 3.0,
            "exhaustion_decay_ratio": 0.7,
            "thresholds": {"low": 1.0, "medium": 1.5, "high": 2.0},
        },
        store=store,
    )

    result = signal.evaluate("BTC")

    assert result is not None
    assert result.direction == "SHORT_BIAS"
    assert result.metadata["sub_signal"] == "cascade_continuation"
    store.close()


def test_funding_velocity_signal_detects_crowding_acceleration(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "funding_velocity.db"))
    now_ms = int(time.time() * 1000)
    hour_ms = 3600 * 1000

    rates = [0.00010, 0.00011, 0.00013, 0.00016, 0.00021, 0.00027, 0.00034]
    for idx, rate in enumerate(rates):
        store.add_funding("BTC", rate, premium=0.0, ts=now_ms - (len(rates) - idx) * hour_ms)

    store.add_snapshot("BTC", funding=rates[0], oi=1_000_000, mark_px=100_000, oracle_px=100_000, premium=0.0)
    store.add_snapshot("BTC", funding=rates[-1], oi=1_000_000, mark_px=100_050, oracle_px=100_050, premium=0.0)

    signal = FundingVelocitySignal(
        name="funding_velocity",
        config={
            "lookback_hours": 24,
            "min_samples": 7,
            "velocity_window": 3,
            "velocity_threshold": 0.00001,
            "acceleration_threshold": 0.000005,
            "price_divergence_threshold_pct": 0.25,
            "thresholds": {"low": 1.0, "medium": 1.5, "high": 2.0},
        },
        store=store,
    )

    result = signal.evaluate("BTC")

    assert result is not None
    assert result.direction == "SHORT_BIAS"
    assert result.metadata["acceleration"] > 0
    store.close()


def test_leverage_flush_signal_detects_post_long_flush_reversal(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "leverage_flush.db"))
    now_ms = int(time.time() * 1000)

    start_ts = now_ms - 55 * 60 * 1000
    end_ts = now_ms - 1_000
    store.add_snapshot("BTC", funding=0.0, oi=1_000_000, mark_px=100_000, oracle_px=100_000, premium=0.0)
    store.add_snapshot("BTC", funding=0.0, oi=940_000, mark_px=98_500, oracle_px=98_500, premium=0.0)
    store.add_oi("BTC", 1_000_000, start_ts)
    store.add_oi("BTC", 940_000, end_ts)

    for idx in range(4):
        store.add_liquidation("BTC", "B", 99_500, 1.0, start_ts + idx * 60_000)
    for idx in range(4):
        store.add_liquidation("BTC", "B", 98_700, 0.4, now_ms - 20 * 60 * 1000 + idx * 60_000)

    signal = LeverageFlushSignal(
        name="leverage_flush",
        config={
            "lookback_minutes": 60,
            "min_liquidation_events": 6,
            "oi_drop_threshold_pct": 3.0,
            "price_displacement_threshold_pct": 0.75,
            "liquidation_share_threshold": 0.6,
            "decline_confirmation_ratio": 0.8,
            "thresholds": {"low": 1.0, "medium": 1.5, "high": 2.0},
        },
        store=store,
    )

    result = signal.evaluate("BTC")

    assert result is not None
    assert result.direction == "LONG_BIAS"
    assert result.metadata["sub_signal"] == "post_long_flush"
    store.close()


def test_signal_engine_matches_new_regime(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "engine.db"))
    config_dir = Path(__file__).resolve().parents[1] / "config"
    engine = SignalEngine(config_dir=str(config_dir), global_config={"assets": ["BTC"]}, store=store)

    regime, conviction = engine._match_regime({"leverage_flush", "liquidation_cascade"}, None)

    assert regime == "Leverage Flush"
    assert conviction == "HIGH"
    store.close()
