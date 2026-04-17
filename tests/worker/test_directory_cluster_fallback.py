"""Tests for the directory-clustering assignment fallback."""

from __future__ import annotations

from worker.pipeline.wiki_planner import _directory_cluster_assign


def _outline(*titles_and_purposes: tuple[str, str]) -> list[dict]:
    return [{"title": t, "purpose": p} for t, p in titles_and_purposes]


def test_preserves_directory_locality():
    """Files in the same directory end up on the same page."""
    outline = _outline(
        ("Worker Pipeline", "Stage-by-stage generation pipeline."),
        ("API Layer", "REST and WebSocket endpoints."),
        ("Web Frontend", "Next.js user interface."),
    )
    all_files = [
        "worker/pipeline/wiki_planner.py",
        "worker/pipeline/page_generator.py",
        "worker/pipeline/ast_analysis.py",
        "api/routers/repos.py",
        "api/routers/wiki.py",
        "web/components/WikiPage.tsx",
        "web/app/page.tsx",
    ]

    result = _directory_cluster_assign(outline, all_files)

    assert "worker/pipeline/wiki_planner.py" in result["Worker Pipeline"]
    assert "worker/pipeline/page_generator.py" in result["Worker Pipeline"]
    assert "worker/pipeline/ast_analysis.py" in result["Worker Pipeline"]
    assert "api/routers/repos.py" in result["API Layer"]
    assert "api/routers/wiki.py" in result["API Layer"]
    assert "web/components/WikiPage.tsx" in result["Web Frontend"]
    assert "web/app/page.tsx" in result["Web Frontend"]


def test_all_files_assigned_exactly_once():
    """Every file must be assigned to exactly one page."""
    outline = _outline(
        ("Overview", "Project overview."),
        ("Core", "Core logic."),
    )
    all_files = [f"src/mod{i}.py" for i in range(12)]
    result = _directory_cluster_assign(outline, all_files)
    flat = [f for files in result.values() for f in files]
    assert sorted(flat) == sorted(all_files)
    assert len(flat) == len(set(flat))


def test_unmatched_files_go_to_overview_when_present():
    """Files that don't match any directory fall to 'Overview' page."""
    outline = _outline(
        ("Overview", "High-level overview."),
        ("Core Pipeline", "Stage pipeline."),
    )
    all_files = [
        "worker/pipeline/a.py",  # matches "Core Pipeline"
        "README.md",  # no clear directory match
        "LICENSE",  # no clear directory match
    ]
    result = _directory_cluster_assign(outline, all_files)
    assert "worker/pipeline/a.py" in result["Core Pipeline"]
    assert "README.md" in result["Overview"]
    assert "LICENSE" in result["Overview"]


def test_unmatched_files_go_to_first_page_when_no_overview():
    """With no Overview page, unmatched files land on the first page."""
    outline = _outline(
        ("First", "First page."),
        ("Second", "Second page."),
    )
    all_files = ["mystery_file.txt"]
    result = _directory_cluster_assign(outline, all_files)
    assert result["First"] == ["mystery_file.txt"]
    assert result["Second"] == []


def test_splits_oversized_directory_groups_across_matching_pages():
    """If one directory has > 50 files but multiple pages match it, split evenly."""
    from worker.pipeline.wiki_planner import MAX_FILES_PER_PAGE

    outline = _outline(
        ("Worker Pipeline Part A", "First half of worker pipeline."),
        ("Worker Pipeline Part B", "Second half of worker pipeline."),
    )
    # Use more than MAX_FILES_PER_PAGE to trigger split
    file_count = MAX_FILES_PER_PAGE + 10
    all_files = [f"worker/pipeline/file{i}.py" for i in range(file_count)]
    result = _directory_cluster_assign(outline, all_files)
    # Both pages get roughly half
    page_a_count = len(result["Worker Pipeline Part A"])
    page_b_count = len(result["Worker Pipeline Part B"])
    assert page_a_count + page_b_count == file_count
    # Neither page exceeds the 50-file cap
    assert page_a_count <= MAX_FILES_PER_PAGE
    assert page_b_count <= MAX_FILES_PER_PAGE
