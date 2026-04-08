"""
Tests for exponential backoff retry logic in BybitClient._get().

Mocks aiohttp.ClientSession.get to simulate HTTP errors and network failures
without making real network calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from data.bybit_client import BybitClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status: int = 200, json_body: dict | None = None) -> MagicMock:
    """Build a mock async context manager that mimics aiohttp's response."""
    if json_body is None:
        json_body = {"retCode": 0, "result": {"list": []}}

    # Use MagicMock for resp so raise_for_status() is a normal sync call
    resp = MagicMock()
    resp.status = status

    if status >= 400:
        resp.raise_for_status.side_effect = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=status,
        )
    else:
        resp.raise_for_status.return_value = None
        # resp.json() must be awaitable — wrap in AsyncMock
        resp.json = AsyncMock(return_value=json_body)

    # Make it usable as an async context manager
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_client() -> BybitClient:
    client = BybitClient()
    client._session = MagicMock()
    client._session.closed = False
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_500_retries_and_succeeds_on_second_attempt():
    """A 500 on the first attempt should trigger a retry; success on attempt 2."""
    client = _make_client()

    success_body = {"retCode": 0, "result": {"key": "value"}}
    responses = [
        _make_response(status=500),
        _make_response(status=200, json_body=success_body),
    ]
    client._session.get.side_effect = responses

    with patch("data.bybit_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client._get("/v5/market/kline", {"symbol": "BTCUSDT"})

    assert result == {"key": "value"}
    assert client._session.get.call_count == 2
    mock_sleep.assert_awaited_once_with(1.0)  # delay for attempt 0: 1.0 * 2^0 = 1s


@pytest.mark.anyio
async def test_500_exhausts_all_retries_returns_none():
    """Persistent 500 errors should exhaust all retries and return None."""
    client = _make_client()

    # 4 calls total: initial + 3 retries
    client._session.get.side_effect = [_make_response(status=500)] * 4

    with patch("data.bybit_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client._get("/v5/market/kline", {"symbol": "BTCUSDT"})

    assert result is None
    assert client._session.get.call_count == 4  # 1 initial + 3 retries
    assert mock_sleep.await_count == 3
    # Verify exponential delays: 1s, 2s, 4s
    delays = [call.args[0] for call in mock_sleep.await_args_list]
    assert delays == [1.0, 2.0, 4.0]


@pytest.mark.anyio
async def test_4xx_does_not_retry():
    """A 4xx error should return None immediately without retrying."""
    client = _make_client()

    client._session.get.side_effect = [_make_response(status=400)]

    with patch("data.bybit_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client._get("/v5/market/kline", {"symbol": "BTCUSDT"})

    assert result is None
    assert client._session.get.call_count == 1  # no retries
    mock_sleep.assert_not_awaited()


@pytest.mark.anyio
async def test_network_error_retries():
    """aiohttp.ClientError (network errors) should trigger retries."""
    client = _make_client()

    success_body = {"retCode": 0, "result": {"data": 42}}
    success_cm = _make_response(status=200, json_body=success_body)

    # Simulate connection error then success
    network_err_cm = MagicMock()
    network_err_cm.__aenter__ = AsyncMock(
        side_effect=aiohttp.ClientConnectionError("connection reset")
    )
    network_err_cm.__aexit__ = AsyncMock(return_value=False)

    client._session.get.side_effect = [network_err_cm, success_cm]

    with patch("data.bybit_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client._get("/v5/market/kline", {"symbol": "BTCUSDT"})

    assert result == {"data": 42}
    assert client._session.get.call_count == 2
    mock_sleep.assert_awaited_once_with(1.0)


@pytest.mark.anyio
async def test_ret_code_nonzero_does_not_retry():
    """Bybit API-level errors (retCode != 0) are logic errors and must not be retried."""
    client = _make_client()

    error_body = {"retCode": 10001, "retMsg": "params error", "result": None}
    client._session.get.side_effect = [_make_response(status=200, json_body=error_body)]

    with patch("data.bybit_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client._get("/v5/market/kline", {"symbol": "BTCUSDT"})

    assert result is None
    assert client._session.get.call_count == 1  # no retries
    mock_sleep.assert_not_awaited()


@pytest.mark.anyio
async def test_network_error_exhausts_retries_returns_none():
    """Persistent network errors should exhaust retries and return None."""
    client = _make_client()

    def _make_network_err_cm():
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("timeout")
        )
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    client._session.get.side_effect = [_make_network_err_cm() for _ in range(4)]

    with patch("data.bybit_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client._get("/v5/market/kline", {"symbol": "BTCUSDT"})

    assert result is None
    assert client._session.get.call_count == 4
    assert mock_sleep.await_count == 3
