"""Tests for Stage 3 batched file assignment."""

from __future__ import annotations

from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.wiki_planner import (
    _build_batch_assignment_system,
    _build_batch_assignment_user,
)


def test_system_segment_is_cacheable():
    """The static context (outline + file_summary + dep_info) must be cacheable."""
    outline = [
        {"title": "Overview", "purpose": "top"},
        {"title": "Core", "purpose": "core"},
    ]
    segments = _build_batch_assignment_system(
        outline=outline,
        file_summary="file summary text",
        dep_info="dep info text",
    )
    assert isinstance(segments, list)
    assert all(isinstance(s, PromptSegment) for s in segments)
    assert any(s.cacheable for s in segments), (
        "at least one system segment must be cacheable"
    )
    # Outline and file_summary content must be present
    joined = "".join(s.text for s in segments)
    assert "Overview" in joined
    assert "file summary text" in joined
    assert "dep info text" in joined


def test_user_segment_contains_only_batch_files():
    """The user segment must contain only the per-batch file list, not the full repo."""
    batch = ["a.py", "b.py", "c.py"]
    segment = _build_batch_assignment_user(batch_files=batch, outline_titles=["O", "C"])
    assert isinstance(segment, PromptSegment)
    assert segment.cacheable is False
    for f in batch:
        assert f in segment.text
    # The outline titles are included as enum reminders
    assert "O" in segment.text and "C" in segment.text
    # Not cached, not containing full context
    assert "file summary" not in segment.text
