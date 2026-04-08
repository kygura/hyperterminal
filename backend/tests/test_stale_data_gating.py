from __future__ import annotations

import asyncio
import time

from alerts import AlertManager
from db.store import SQLiteDataStore
from engine.signal_engine import TradeCandidate
from main_daemon import process_signal_candidates
from signals.base import SignalResult


class _EngineStub:
    def __init__(self, candidate: TradeCandidate, required: set[str]) -> None:
        self._candidate = candidate
        self._required = required

    async def evaluate_all(self, coins: list[str]) -> list[SignalResult]:
        return list(self._candidate.signals)

    def score_confluence(self, results: list[SignalResult]) -> list[TradeCandidate]:
        return [self._candidate]

    def required_datasets_for_candidate(self, candidate: TradeCandidate) -> set[str]:
        return set(self._required)


def _candidate() -> TradeCandidate:
    signal = SignalResult(
        signal_name="funding_extremes",
        coin="BTC",
        direction="LONG_BIAS",
        strength=1.0,
        priority="HIGH",
        message="signal",
        timestamp=time.time(),
    )
    return TradeCandidate(
        coin="BTC",
        direction="LONG_BIAS",
        regime="Funding Fade",
        conviction="MEDIUM",
        signals=[signal],
        timestamp=time.time(),
    )


def test_process_signal_candidates_enforce_staleness_blocks_candidate(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "stale_enforce.db"))
    engine = _EngineStub(_candidate(), {"funding_rates"})
    alert_manager = AlertManager(cooldown_seconds=1, cadence="hourly")

    asyncio.run(
        process_signal_candidates(
            engine=engine,
            alert_manager=alert_manager,
            store=store,
            telegram=None,
            telegram_queue=None,
            coins=["BTC"],
            dry_run=True,
            timeframe="hourly",
            freshness_mode="enforce",
            freshness_thresholds={"funding_rates": 1},
        )
    )

    assert store.get_recent_candidates() == []
    store.close()


def test_process_signal_candidates_warn_mode_persists_candidate(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "stale_warn.db"))
    engine = _EngineStub(_candidate(), {"funding_rates"})
    alert_manager = AlertManager(cooldown_seconds=1, cadence="hourly")

    asyncio.run(
        process_signal_candidates(
            engine=engine,
            alert_manager=alert_manager,
            store=store,
            telegram=None,
            telegram_queue=None,
            coins=["BTC"],
            dry_run=True,
            timeframe="hourly",
            freshness_mode="warn",
            freshness_thresholds={"funding_rates": 1},
        )
    )

    assert len(store.get_recent_candidates()) == 1
    store.close()


def test_process_signal_candidates_dedupes_same_bucket(tmp_path):
    store = SQLiteDataStore(db_path=str(tmp_path / "dedupe.db"))
    candidate = _candidate()
    engine = _EngineStub(candidate, set())
    alert_manager = AlertManager(cooldown_seconds=0, cadence="hourly")

    asyncio.run(
        process_signal_candidates(
            engine=engine,
            alert_manager=alert_manager,
            store=store,
            telegram=None,
            telegram_queue=None,
            coins=["BTC"],
            dry_run=True,
            timeframe="hourly",
            freshness_mode="off",
            freshness_thresholds={},
        )
    )
    second_manager = AlertManager(cooldown_seconds=0, cadence="hourly")
    asyncio.run(
        process_signal_candidates(
            engine=engine,
            alert_manager=second_manager,
            store=store,
            telegram=None,
            telegram_queue=None,
            coins=["BTC"],
            dry_run=True,
            timeframe="hourly",
            freshness_mode="off",
            freshness_thresholds={},
        )
    )

    assert len(store.get_recent_candidates()) == 1
    store.close()
