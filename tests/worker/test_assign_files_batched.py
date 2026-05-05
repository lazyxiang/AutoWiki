"""Tests for page-centric file selection prompt builders and batch executor."""

from __future__ import annotations

from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.wiki_planner import (
    _SELECTION_SCHEMA,
    MAX_FILES_PER_PAGE,
    _build_selection_system,
    _build_selection_user,
)


def test_selection_system_segment_is_cacheable():
    segments = _build_selection_system(
        file_summary="file summary text",
        dep_info="dep info text",
    )
    assert isinstance(segments, list)
    assert all(isinstance(s, PromptSegment) for s in segments)
    assert any(s.cacheable for s in segments)
    joined = "".join(s.text for s in segments)
    assert "file summary text" in joined
    assert "dep info text" in joined


def test_selection_system_segment_no_dep_info():
    segments = _build_selection_system(file_summary="summary", dep_info=None)
    joined = "".join(s.text for s in segments)
    assert "Dependency" not in joined


def test_selection_user_segment_contains_pages_and_candidates():
    pages_with_candidates = [
        (
            "API Gateway",
            "Routes HTTP requests.",
            ["api/routes.py", "api/models.py"],
            5,
        ),
        ("Worker", "Background jobs.", ["worker/job.py"], 2),
    ]
    seg = _build_selection_user(pages_with_candidates)
    assert isinstance(seg, PromptSegment)
    assert seg.cacheable is False
    assert "API Gateway" in seg.text
    assert "Worker" in seg.text
    assert "api/routes.py" in seg.text
    assert "worker/job.py" in seg.text


def test_selection_user_segment_renders_per_page_target_and_cap():
    pages_with_candidates = [
        ("Core", "Core logic.", ["core/a.py"], 4),
        ("API", "REST API.", ["api/routes.py", "api/models.py"], 6),
    ]
    seg = _build_selection_user(pages_with_candidates)
    assert "Target: 4 files" in seg.text
    assert "Target: 6 files" in seg.text
    assert str(MAX_FILES_PER_PAGE) in seg.text


def test_selection_user_segment_requests_relevance_ordering():
    pages_with_candidates = [
        ("Core", "Core logic.", ["core/a.py", "core/b.py"], 3),
    ]
    seg = _build_selection_user(pages_with_candidates)

    assert "{path, relevance}" in seg.text
    assert "most-representative-first" in seg.text
    assert "non-increasing" in seg.text
    assert "first file" in seg.text
    assert ">= 3" in seg.text


def test_selection_user_segment_injects_last_error():
    pages_with_candidates = [("API", "REST API.", ["api/routes.py"], 3)]
    seg = _build_selection_user(
        pages_with_candidates, last_error="Too many files on page X"
    )
    assert "Too many files on page X" in seg.text
    assert "CRITICAL" in seg.text


def test_selection_schema_has_selections_key():
    assert "selections" in _SELECTION_SCHEMA["properties"]
    item_props = _SELECTION_SCHEMA["properties"]["selections"]["items"]["properties"]
    assert "page_title" in item_props
    assert "files" in item_props


def test_selection_schema_files_are_relevance_objects():
    item_props = _SELECTION_SCHEMA["properties"]["selections"]["items"]["properties"]
    file_items = item_props["files"]["items"]

    assert file_items["type"] == "object"
    assert file_items["required"] == ["path", "relevance"]
    assert file_items["properties"]["path"] == {"type": "string"}
    assert file_items["properties"]["relevance"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 10,
    }
