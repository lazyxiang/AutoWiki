"""Tests for worker.utils.retry — async exponential backoff retry."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from worker.utils.retry import TRANSIENT_EXCEPTIONS, async_retry


async def test_success_on_first_try():
    fn = AsyncMock(return_value="ok")
    result = await async_retry(fn, "arg", key="val")
    assert result == "ok"
    fn.assert_awaited_once_with("arg", key="val")


async def test_retries_on_transient_exception_succeeds():
    fn = AsyncMock(side_effect=[TimeoutError("timeout"), "ok"])
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await async_retry(fn, max_retries=3, initial_delay=0.1)
    assert result == "ok"
    assert fn.await_count == 2


async def test_raises_after_exhausting_retries():
    fn = AsyncMock(side_effect=TimeoutError("always fails"))
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(TimeoutError):
            await async_retry(fn, max_retries=3, initial_delay=0.1)
    assert fn.await_count == 3


async def test_non_transient_exception_propagates_immediately():
    fn = AsyncMock(side_effect=ValueError("not transient"))
    with pytest.raises(ValueError):
        await async_retry(fn, max_retries=3, initial_delay=0.1)
    # Should fail on first attempt, no retries
    fn.assert_awaited_once()


async def test_on_retry_callback_called():
    calls: list[tuple] = []

    async def on_retry(attempt: int, max_retries: int, wait: float, exc: Exception):
        calls.append((attempt, max_retries, wait, type(exc).__name__))

    fn = AsyncMock(side_effect=[TimeoutError("t1"), TimeoutError("t2"), "done"])
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await async_retry(
            fn, max_retries=3, initial_delay=2.0, on_retry=on_retry
        )
    assert result == "done"
    assert len(calls) == 2
    assert calls[0] == (1, 3, 2.0, "TimeoutError")
    assert calls[1] == (2, 3, 4.0, "TimeoutError")  # backoff applied


async def test_exponential_backoff_delays():
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    fn = AsyncMock(side_effect=[TimeoutError(), TimeoutError(), TimeoutError(), "ok"])
    with patch("worker.utils.retry.asyncio.sleep", side_effect=fake_sleep):
        result = await async_retry(
            fn,
            max_retries=4,
            initial_delay=2.0,
            backoff_factor=2.0,
            max_delay=60.0,
        )
    assert result == "ok"
    assert sleep_calls == [2.0, 4.0, 8.0]


async def test_max_delay_cap():
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    fn = AsyncMock(side_effect=[TimeoutError()] * 4 + ["ok"])
    with patch("worker.utils.retry.asyncio.sleep", side_effect=fake_sleep):
        await async_retry(
            fn,
            max_retries=5,
            initial_delay=30.0,
            backoff_factor=4.0,
            max_delay=45.0,
        )
    assert all(d <= 45.0 for d in sleep_calls)


async def test_max_retries_one_means_no_retry():
    fn = AsyncMock(side_effect=TimeoutError("fail"))
    with pytest.raises(TimeoutError):
        await async_retry(fn, max_retries=1)
    fn.assert_awaited_once()


async def test_transient_exceptions_tuple_not_empty():
    assert len(TRANSIENT_EXCEPTIONS) > 0
    assert TimeoutError in TRANSIENT_EXCEPTIONS
    assert asyncio.TimeoutError in TRANSIENT_EXCEPTIONS
    assert OSError in TRANSIENT_EXCEPTIONS
