"""Shared logging helpers for pipeline retry loops and fallback paths.

Every stage that catches ``ValueError`` / ``json.JSONDecodeError`` /
``KeyError`` from an LLM response must use these helpers so the resulting
logs have a consistent shape.  Without them, validation failures are
silently swallowed by the retry loop and the user sees a degraded wiki
with no clue where the degradation originated.
"""

from __future__ import annotations

import logging
from typing import Any

_MAX_CONTEXT_VALUE_LEN = 500


def _format_context(context: dict[str, Any] | None) -> str:
    """Render a context dict as ``key=value`` pairs, truncating long values."""
    if not context:
        return ""
    parts: list[str] = []
    for key, value in context.items():
        text = str(value)
        if len(text) > _MAX_CONTEXT_VALUE_LEN:
            text = text[:_MAX_CONTEXT_VALUE_LEN] + "...(truncated)"
        parts.append(f"{key}={text}")
    return " ".join(parts)


def log_validation_retry(
    logger: logging.Logger,
    *,
    stage: str,
    attempt: int,
    max_retries: int,
    exc: Exception,
    context: dict[str, Any] | None = None,
) -> None:
    """Log a *recoverable* validation/parse failure from a pipeline retry loop.

    Called inside ``except (ValueError, json.JSONDecodeError, KeyError)``
    blocks to record what the LLM produced and why it was rejected, so the
    next retry's failure mode is visible.  Always emits ``WARNING``.
    """
    ctx = _format_context(context)
    suffix = f" | {ctx}" if ctx else ""
    logger.warning(
        "%s: validation failed on attempt %d/%d: %s%s",
        stage,
        attempt,
        max_retries,
        exc,
        suffix,
    )


def log_final_failure(
    logger: logging.Logger,
    *,
    stage: str,
    exc: Exception,
    context: dict[str, Any] | None = None,
) -> None:
    """Log an *exhausted* retry loop or fallback invocation.

    Always emits ``ERROR`` with ``exc_info=True`` so the full traceback is
    captured.  Use this when the pipeline is about to hand off to a
    deterministic heuristic fallback or return a degraded result.
    """
    ctx = _format_context(context)
    suffix = f" | {ctx}" if ctx else ""
    exc_info = (type(exc), exc, exc.__traceback__) if exc.__traceback__ else None
    logger.error(
        "%s: all retries exhausted: %s%s",
        stage,
        exc,
        suffix,
        exc_info=exc_info,
        stack_info=exc_info is None,
    )
