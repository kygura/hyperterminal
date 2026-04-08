"""
Tests for the min_conviction filter in process_signal_candidates.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from main_daemon import process_signal_candidates, _CONVICTION_RANK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_candidate(coin: str, conviction: str):
    """Create a minimal mock TradeCandidate."""
    return SimpleNamespace(
        coin=coin,
        direction="LONG",
        conviction=conviction,
        regime="NEUTRAL",
        signals=[],
        vwap_state=None,
    )


def make_engine(candidates: list):
    """Return a mock SignalEngine whose evaluate_all/score_confluence return the given candidates."""
    engine = MagicMock()
    engine.evaluate_all = AsyncMock(return_value={})
    engine.score_confluence = MagicMock(return_value=candidates)
    return engine


def make_alert_manager(should_fire: bool = True):
    """Return an AlertManager mock."""
    am = MagicMock()
    am.should_fire = MagicMock(return_value=should_fire)
    am.record_fire = MagicMock()
    am.format_alert = MagicMock(return_value="alert")
    am.should_telegram = MagicMock(return_value=False)
    return am


def make_store():
    store = MagicMock()
    store.add_trade_candidate = MagicMock()
    return store


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_conviction_rank_values():
    """Sanity-check the ranking constant."""
    assert _CONVICTION_RANK["LOW"] < _CONVICTION_RANK["MEDIUM"] < _CONVICTION_RANK["HIGH"]


def test_min_conviction_high_filters_low_and_medium():
    """When min_conviction='HIGH', LOW and MEDIUM candidates must be dropped."""
    candidates = [
        make_candidate("BTC", "HIGH"),
        make_candidate("ETH", "MEDIUM"),
        make_candidate("SOL", "LOW"),
    ]
    engine = make_engine(candidates)
    alert_manager = make_alert_manager(should_fire=True)
    store = make_store()

    on_candidate_calls = []

    async def on_candidate(c):
        on_candidate_calls.append(c)

    async def _run():
        with patch("main_daemon.serialize_candidate", side_effect=lambda c, tf: {"coin": c.coin}):
            await process_signal_candidates(
                engine=engine,
                alert_manager=alert_manager,
                store=store,
                telegram=None,
                coins=["BTC", "ETH", "SOL"],
                dry_run=True,
                timeframe="hourly",
                on_candidate=on_candidate,
                min_conviction="HIGH",
            )

    run(_run())

    # Only the HIGH candidate should have been processed
    assert len(on_candidate_calls) == 1
    assert on_candidate_calls[0]["coin"] == "BTC"
    assert alert_manager.record_fire.call_count == 1


def test_min_conviction_none_passes_all():
    """When min_conviction=None (default), all candidates pass through."""
    candidates = [
        make_candidate("BTC", "HIGH"),
        make_candidate("ETH", "MEDIUM"),
        make_candidate("SOL", "LOW"),
    ]
    engine = make_engine(candidates)
    alert_manager = make_alert_manager(should_fire=True)
    store = make_store()

    on_candidate_calls = []

    async def on_candidate(c):
        on_candidate_calls.append(c)

    async def _run():
        with patch("main_daemon.serialize_candidate", side_effect=lambda c, tf: {"coin": c.coin}):
            await process_signal_candidates(
                engine=engine,
                alert_manager=alert_manager,
                store=store,
                telegram=None,
                coins=["BTC", "ETH", "SOL"],
                dry_run=True,
                timeframe="hourly",
                on_candidate=on_candidate,
                # min_conviction not passed → defaults to None
            )

    run(_run())

    assert len(on_candidate_calls) == 3
    coins_seen = {c["coin"] for c in on_candidate_calls}
    assert coins_seen == {"BTC", "ETH", "SOL"}


def test_min_conviction_medium_filters_low_only():
    """When min_conviction='MEDIUM', only LOW candidates are filtered."""
    candidates = [
        make_candidate("BTC", "HIGH"),
        make_candidate("ETH", "MEDIUM"),
        make_candidate("SOL", "LOW"),
    ]
    engine = make_engine(candidates)
    alert_manager = make_alert_manager(should_fire=True)
    store = make_store()

    on_candidate_calls = []

    async def on_candidate(c):
        on_candidate_calls.append(c)

    async def _run():
        with patch("main_daemon.serialize_candidate", side_effect=lambda c, tf: {"coin": c.coin}):
            await process_signal_candidates(
                engine=engine,
                alert_manager=alert_manager,
                store=store,
                telegram=None,
                coins=["BTC", "ETH", "SOL"],
                dry_run=True,
                timeframe="hourly",
                on_candidate=on_candidate,
                min_conviction="MEDIUM",
            )

    run(_run())

    assert len(on_candidate_calls) == 2
    coins_seen = {c["coin"] for c in on_candidate_calls}
    assert coins_seen == {"BTC", "ETH"}


def test_no_candidates_empty_list():
    """No candidates → function runs without error regardless of min_conviction."""
    engine = make_engine([])
    alert_manager = make_alert_manager(should_fire=False)
    store = make_store()

    async def _run():
        await process_signal_candidates(
            engine=engine,
            alert_manager=alert_manager,
            store=store,
            telegram=None,
            coins=["BTC"],
            dry_run=True,
            timeframe="hourly",
            min_conviction="HIGH",
        )

    run(_run())

    alert_manager.record_fire.assert_not_called()
