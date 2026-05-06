"""Tests for page_notes and repo_notes injection into draft prompts."""

from __future__ import annotations


def test_build_draft_prompt_includes_page_notes():
    """page_notes from WikiPageSpec appear in the draft prompt."""
    from worker.pipeline.page.draft import build_draft_prompt
    from worker.pipeline.page.outline import PageOutline
    from worker.pipeline.planner.wiki_planner import WikiPageSpec

    spec = WikiPageSpec(
        title="Core Module",
        purpose="Core logic.",
        page_notes=[{"content": "Key invariant: X must always be locked."}],
    )
    outline = PageOutline(sections=[], key_claims=["claim1", "claim2", "claim3"])

    segments = build_draft_prompt(spec, outline, [], "myrepo")
    combined = "\n".join(s.text for s in segments)
    assert "Key invariant: X must always be locked." in combined


def test_build_draft_prompt_includes_repo_notes():
    """repo_notes appear in the draft prompt when provided."""
    from worker.pipeline.page.draft import build_draft_prompt
    from worker.pipeline.page.outline import PageOutline
    from worker.pipeline.planner.wiki_planner import WikiPageSpec

    spec = WikiPageSpec(title="Overview", purpose="Top-level page.")
    outline = PageOutline(sections=[], key_claims=["claim1", "claim2", "claim3"])

    segments = build_draft_prompt(
        spec,
        outline,
        [],
        "myrepo",
        repo_notes=[{"content": "This repo targets Python 3.12 only."}],
    )
    combined = "\n".join(s.text for s in segments)
    assert "Python 3.12 only" in combined
