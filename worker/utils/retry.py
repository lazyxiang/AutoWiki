"""Async exponential backoff retry for transient LLM/embedding errors."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
OnRetryCallback = Callable[[int, int, float, Exception], Awaitable[None]]

# Build transient exception tuple by probing installed provider SDKs
_TRANSIENT: list[type[Exception]] = [TimeoutError, asyncio.TimeoutError, OSError]

try:
    import anthropic

    _TRANSIENT += [
        anthropic.APITimeoutError,
        anthropic.RateLimitError,
        anthropic.APIConnectionError,
        anthropic.InternalServerError,
    ]
except ImportError:
    pass

try:
    import openai

    _TRANSIENT += [
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.APIConnectionError,
        openai.InternalServerError,
    ]
except ImportError:
    pass

TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = tuple(_TRANSIENT)


async def async_retry(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    initial_delay: float = 2.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    transient_exceptions: tuple[type[Exception], ...] = TRANSIENT_EXCEPTIONS,
    on_retry: OnRetryCallback | None = None,
    **kwargs: Any,
) -> T:
    """Call fn(*args, **kwargs), retrying up to max_retries times on transient errors.

    Args:
        fn: Async callable to invoke.
        *args: Positional arguments forwarded to fn.
        max_retries: Total attempts (1 = no retry).
        initial_delay: Seconds to wait before first retry.
        backoff_factor: Multiplier applied to delay after each retry.
        max_delay: Upper bound on wait time in seconds.
        transient_exceptions: Exception types that trigger a retry.
        on_retry: Async callback invoked before each sleep with
            (attempt, max_retries, wait_seconds, exception).
        **kwargs: Keyword arguments forwarded to fn.
    """
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            return await fn(*args, **kwargs)
        except transient_exceptions as exc:
            if attempt == max_retries - 1:
                raise
            wait = min(delay, max_delay)
            logger.warning(
                "Transient error (attempt %d/%d): %s — retrying in %.0fs.",
                attempt + 1,
                max_retries,
                exc,
                wait,
            )
            if on_retry is not None:
                await on_retry(attempt + 1, max_retries, wait, exc)
            await asyncio.sleep(wait)
            delay *= backoff_factor
    raise AssertionError("unreachable")  # pragma: no cover
