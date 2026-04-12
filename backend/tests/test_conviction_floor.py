import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from main_daemon import _CONVICTION_RANK, process_signal_candidates


def make_candidate(coin: str, conviction: str):
    return SimpleNamespace(
        coin=coin,
        direction="LONG",
        conviction=conviction,
        regime="NEUTRAL",
        signals=[],
        vwap_state=None,
        timestamp=1710000000.0,
    )


def make_engine(candidates: list):
    engine = MagicMock()
    engine.evaluate_all = AsyncMock(return_value={})
    engine.score_confluence = MagicMock(return_value=candidates)
    engine.required_datasets_for_candidate = MagicMock(return_value=[])
    return engine


def make_alert_manager(should_fire: bool = True):
    manager = MagicMock()
    manager.should_fire = MagicMock(return_value=should_fire)
    manager.record_fire = MagicMock()
    manager.format_alert = MagicMock(return_value="alert")
    manager.should_telegram = MagicMock(return_value=False)
    manager.delivery_bucket_for = MagicMock(return_value="2026-04-13T00")
    manager.candidate_fingerprint = MagicMock(side_effect=lambda candidate: f"{candidate.coin}|{candidate.conviction}")
    return manager


def make_store():
    store = MagicMock()
    store.add_trade_candidate = MagicMock(return_value=True)
    store.get_latest_timestamps = MagicMock(return_value={})
    return store


def run(coro):
    return asyncio.run(coro)


def test_conviction_rank_values():
    assert _CONVICTION_RANK["LOW"] < _CONVICTION_RANK["MEDIUM"] < _CONVICTION_RANK["HIGH"]


def test_min_conviction_high_filters_low_and_medium():
    candidates = [
        make_candidate("BTC", "HIGH"),
        make_candidate("ETH", "MEDIUM"),
        make_candidate("SOL", "LOW"),
    ]
    engine = make_engine(candidates)
    alert_manager = make_alert_manager(should_fire=True)
    store = make_store()
    on_candidate_calls = []

    async def on_candidate(candidate):
        on_candidate_calls.append(candidate)

    async def _run():
        with patch("main_daemon.serialize_candidate", side_effect=lambda c, tf: {"coin": c.coin}):
            await process_signal_candidates(
                engine=engine,
                alert_manager=alert_manager,
                store=store,
                telegram=None,
                telegram_queue=None,
                coins=["BTC", "ETH", "SOL"],
                dry_run=True,
                timeframe="hourly",
                on_candidate=on_candidate,
                min_conviction="HIGH",
            )

    run(_run())

    assert len(on_candidate_calls) == 1
    assert on_candidate_calls[0]["coin"] == "BTC"
    assert alert_manager.record_fire.call_count == 1


def test_min_conviction_none_passes_all():
    candidates = [
        make_candidate("BTC", "HIGH"),
        make_candidate("ETH", "MEDIUM"),
        make_candidate("SOL", "LOW"),
    ]
    engine = make_engine(candidates)
    alert_manager = make_alert_manager(should_fire=True)
    store = make_store()
    on_candidate_calls = []

    async def on_candidate(candidate):
        on_candidate_calls.append(candidate)

    async def _run():
        with patch("main_daemon.serialize_candidate", side_effect=lambda c, tf: {"coin": c.coin}):
            await process_signal_candidates(
                engine=engine,
                alert_manager=alert_manager,
                store=store,
                telegram=None,
                telegram_queue=None,
                coins=["BTC", "ETH", "SOL"],
                dry_run=True,
                timeframe="hourly",
                on_candidate=on_candidate,
            )

    run(_run())

    assert len(on_candidate_calls) == 3
    assert {candidate["coin"] for candidate in on_candidate_calls} == {"BTC", "ETH", "SOL"}


def test_min_conviction_medium_filters_low_only():
    candidates = [
        make_candidate("BTC", "HIGH"),
        make_candidate("ETH", "MEDIUM"),
        make_candidate("SOL", "LOW"),
    ]
    engine = make_engine(candidates)
    alert_manager = make_alert_manager(should_fire=True)
    store = make_store()
    on_candidate_calls = []

    async def on_candidate(candidate):
        on_candidate_calls.append(candidate)

    async def _run():
        with patch("main_daemon.serialize_candidate", side_effect=lambda c, tf: {"coin": c.coin}):
            await process_signal_candidates(
                engine=engine,
                alert_manager=alert_manager,
                store=store,
                telegram=None,
                telegram_queue=None,
                coins=["BTC", "ETH", "SOL"],
                dry_run=True,
                timeframe="hourly",
                on_candidate=on_candidate,
                min_conviction="MEDIUM",
            )

    run(_run())

    assert len(on_candidate_calls) == 2
    assert {candidate["coin"] for candidate in on_candidate_calls} == {"BTC", "ETH"}


def test_no_candidates_empty_list():
    engine = make_engine([])
    alert_manager = make_alert_manager(should_fire=False)
    store = make_store()

    async def _run():
        await process_signal_candidates(
            engine=engine,
            alert_manager=alert_manager,
            store=store,
            telegram=None,
            telegram_queue=None,
            coins=["BTC"],
            dry_run=True,
            timeframe="hourly",
            min_conviction="HIGH",
        )

    run(_run())

    alert_manager.record_fire.assert_not_called()


def test_unknown_min_conviction_raises_value_error():
    engine = make_engine([make_candidate("BTC", "HIGH")])
    alert_manager = make_alert_manager(should_fire=True)
    store = make_store()

    async def _run():
        await process_signal_candidates(
            engine=engine,
            alert_manager=alert_manager,
            store=store,
            telegram=None,
            telegram_queue=None,
            coins=["BTC"],
            dry_run=True,
            timeframe="hourly",
            min_conviction="URGENT",
        )

    with pytest.raises(ValueError, match="Unknown min_conviction"):
        run(_run())
