"""Tests for shared pipeline logging helpers."""

from __future__ import annotations

import logging

import pytest

from worker.pipeline.pipeline_logging import (
    log_final_failure,
    log_structured_event,
    log_validation_retry,
)


def test_log_validation_retry_emits_warning(caplog):
    logger = logging.getLogger("test.pipeline")
    with caplog.at_level(logging.WARNING, logger="test.pipeline"):
        log_validation_retry(
            logger,
            stage="wiki_planner.outline",
            attempt=1,
            max_retries=3,
            exc=ValueError("bad slug"),
            context={"page_count": 12, "total_files": 180},
        )
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.WARNING
    assert "wiki_planner.outline" in rec.message
    assert "attempt 1/3" in rec.message
    assert "bad slug" in rec.message
    assert "page_count=12" in rec.message
    assert "total_files=180" in rec.message


def test_log_final_failure_emits_error_with_exc_info(caplog):
    logger = logging.getLogger("test.pipeline")
    try:
        raise ValueError("exhausted")
    except ValueError as exc:
        with caplog.at_level(logging.ERROR, logger="test.pipeline"):
            log_final_failure(
                logger,
                stage="wiki_planner.assign_files",
                exc=exc,
                context={"batches": 5, "unassigned": 12},
            )
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.ERROR
    assert "wiki_planner.assign_files" in rec.message
    assert "exhausted" in rec.message
    assert rec.exc_info is not None  # exc_info attached
    assert "batches=5" in rec.message
    assert "unassigned=12" in rec.message


def test_log_validation_retry_truncates_long_response(caplog):
    logger = logging.getLogger("test.pipeline")
    long_text = "x" * 5000
    with caplog.at_level(logging.WARNING, logger="test.pipeline"):
        log_validation_retry(
            logger,
            stage="wiki_planner.outline",
            attempt=2,
            max_retries=3,
            exc=ValueError("schema mismatch"),
            context={"raw_response": long_text},
        )
    rec = caplog.records[0]
    # The response should be truncated with an ellipsis marker.
    assert "..." in rec.message
    assert len(rec.message) < 3000


def test_log_structured_event_rejects_error_levels():
    logger = logging.getLogger("test.pipeline")

    with pytest.raises(ValueError, match="log_final_failure"):
        log_structured_event(
            logger, event="wiki_planner.recovered", level=logging.ERROR
        )
