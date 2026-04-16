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


async def test_batched_assignment_collects_all_files():
    """Each batch's assignments are merged into the final result."""
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _assign_files_in_batches

    outline = [
        {"title": "Overview", "purpose": "top"},
        {"title": "Core", "purpose": "core"},
    ]
    all_files = [f"f{i}.py" for i in range(100)]

    # LLM assigns odd → Core, even → Overview, returned per batch
    async def fake_generate_structured(user_seg, schema, system):
        import re

        text = user_seg.text if hasattr(user_seg, "text") else str(user_seg)
        batch = re.findall(r"- (f\d+\.py)", text)
        assignments = [
            {
                "file": f,
                "page_title": "Core" if int(f[1:-3]) % 2 else "Overview",
            }
            for f in batch
        ]
        return {"assignments": assignments}

    llm = AsyncMock()
    llm.generate_structured.side_effect = fake_generate_structured

    result = await _assign_files_in_batches(
        outline=outline,
        file_summary="fs",
        dep_info=None,
        all_files=all_files,
        llm=llm,
        system="sys",
        on_retry=None,
        batch_size=40,
    )

    # Every file assigned exactly once
    flat = [f for files in result.values() for f in files]
    assert sorted(flat) == sorted(all_files)
    # 50 even → Overview, 50 odd → Core
    assert len(result["Overview"]) == 50
    assert len(result["Core"]) == 50
    # Number of LLM calls: ceil(100 / 40) = 3
    assert llm.generate_structured.await_count == 3


async def test_batched_assignment_reuses_system_segment_across_batches():
    """The same cacheable system segment object is passed to every batch call."""
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _assign_files_in_batches

    outline = [{"title": "X", "purpose": "x"}]
    all_files = [f"a{i}.py" for i in range(50)]

    calls: list[object] = []

    async def capture(user_seg, schema, system):
        calls.append(system)
        import re

        text = user_seg.text if hasattr(user_seg, "text") else str(user_seg)
        batch = re.findall(r"- (a\d+\.py)", text)
        return {"assignments": [{"file": f, "page_title": "X"} for f in batch]}

    llm = AsyncMock()
    llm.generate_structured.side_effect = capture

    await _assign_files_in_batches(
        outline=outline,
        file_summary="fs",
        dep_info="deps",
        all_files=all_files,
        llm=llm,
        system="sys",
        on_retry=None,
        batch_size=20,
    )

    # All calls received identical system objects (same identity or same text)
    assert len(calls) == 3
    first = calls[0]
    for other in calls[1:]:
        assert first is other or first == other


async def test_batched_assignment_retries_unassigned_files():
    """Files not assigned in the initial pass are retried in a cleanup batch."""
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _assign_files_in_batches

    outline = [{"title": "X", "purpose": "x"}]
    all_files = [f"f{i}.py" for i in range(10)]

    call_count = [0]

    async def fake(user_seg, schema, system):
        call_count[0] += 1
        import re

        text = user_seg.text if hasattr(user_seg, "text") else str(user_seg)
        batch = re.findall(r"- (f\d+\.py)", text)
        if call_count[0] == 1:
            # Skip half the files in the first batch
            batch = batch[: len(batch) // 2]
        return {"assignments": [{"file": f, "page_title": "X"} for f in batch]}

    llm = AsyncMock()
    llm.generate_structured.side_effect = fake

    result = await _assign_files_in_batches(
        outline=outline,
        file_summary="fs",
        dep_info=None,
        all_files=all_files,
        llm=llm,
        system="sys",
        on_retry=None,
        batch_size=20,
    )

    # All 10 files assigned despite the first batch dropping half
    assert len(result["X"]) == 10
    # Two calls: initial batch + cleanup batch
    assert call_count[0] == 2
