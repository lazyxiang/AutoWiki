"""Tests for worker.pipeline.page.section_drafter — Pass 2a, Pass 2b, Pass 2c."""

from unittest.mock import AsyncMock

import pytest

from worker.pipeline.page.outline import DiagramPlan, PageOutline, SectionPlan
from worker.pipeline.page.section_drafter import (
    build_skeleton,
    draft_page_in_sections,
    draft_section,
)
from worker.pipeline.planner.wiki_planner import WikiPageSpec


class _StubKeywordIndex:
    def __init__(self):
        self.captured = {}

    def search(self, queries, *, k, files=None, per_file_quota=2):
        self.captured["queries"] = queries
        self.captured["k"] = k
        self.captured["files"] = files
        self.captured["per_file_quota"] = per_file_quota
        return []


async def test_skeleton_returns_markdown_with_h1_and_section_headings():
    outline = PageOutline(
        sections=[
            SectionPlan(heading="Phase 1: Outline", kind="prose", focus="..."),
            SectionPlan(heading="Phase 2: File Selection", kind="prose", focus="..."),
        ],
        key_claims=["a", "b", "c"],
    )
    fast_llm = AsyncMock()
    fast_llm.generate.return_value = (
        "# Wiki Planner\n\n"
        "## Phase 1: Outline\nDescribes the LLM outline pass.\n\n"
        "## Phase 2: File Selection\nDescribes file-to-page assignment.\n"
    )
    md = await build_skeleton(title="Wiki Planner", outline=outline, fast_llm=fast_llm)
    assert md.startswith("# Wiki Planner")
    assert "## Phase 1: Outline" in md
    assert "## Phase 2: File Selection" in md
    fast_llm.generate.assert_awaited_once()


async def test_skeleton_user_prompt_lists_sections_in_order():
    outline = PageOutline(
        sections=[
            SectionPlan(heading="Alpha", kind="prose", focus="alpha purpose"),
            SectionPlan(heading="Beta", kind="prose", focus="beta purpose"),
        ],
        key_claims=["a", "b", "c"],
    )
    fast_llm = AsyncMock()
    fast_llm.generate.return_value = "# T\n\n## Alpha\n## Beta\n"
    await build_skeleton(title="T", outline=outline, fast_llm=fast_llm)
    call_args = fast_llm.generate.call_args
    # build_skeleton invokes generate(prompt) where prompt is a list[PromptSegment].
    # Pin the user segment text contains both headings + focuses in order.
    prompt_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("prompt")
    user_text = prompt_arg[-1].text  # the last segment is the user message
    assert "Alpha" in user_text and "Beta" in user_text
    assert user_text.index("Alpha") < user_text.index("Beta")
    assert "alpha purpose" in user_text


async def test_draft_section_uses_keyword_index_with_section_scope():
    """Scope = spec_files ∩ diagram.source_files when diagram exists."""
    section = SectionPlan(
        heading="Phase 2: File Selection",
        kind="prose+diagram",
        focus="How files are scored",
        diagram=DiagramPlan(
            type="flowchart", purpose="...", source_files=["wiki_planner.py"]
        ),
    )
    stub = _StubKeywordIndex()
    llm = AsyncMock()
    llm.generate.return_value = "## body text"
    await draft_section(
        section=section,
        spec_files=["wiki_planner.py", "page_generator.py"],
        keyword_index=stub,
        llm=llm,
        skeleton_heading="## Phase 2: File Selection",
    )
    assert set(stub.captured["files"]) == {"wiki_planner.py"}
    assert stub.captured["k"] == 8
    assert stub.captured["per_file_quota"] == 2


async def test_draft_section_falls_back_to_spec_files_when_no_diagram():
    """When section has no diagram, full spec_files are used as scope."""
    section = SectionPlan(
        heading="Intro",
        kind="prose",
        focus="overview",
        diagram=None,
    )
    stub = _StubKeywordIndex()
    llm = AsyncMock()
    llm.generate.return_value = "body"
    await draft_section(
        section=section,
        spec_files=["a.py", "b.py"],
        keyword_index=stub,
        llm=llm,
        skeleton_heading="## Intro",
    )
    assert set(stub.captured["files"]) == {"a.py", "b.py"}


async def test_draft_section_passes_heading_and_focus_in_queries():
    """Heading and focus must appear in the queries sent to keyword_index.search."""
    section = SectionPlan(
        heading="Per-section drafting",
        kind="prose",
        focus="how scope intersection works",
        diagram=None,
    )
    stub = _StubKeywordIndex()
    llm = AsyncMock()
    llm.generate.return_value = "body"
    await draft_section(
        section=section,
        spec_files=["x.py"],
        keyword_index=stub,
        llm=llm,
        skeleton_heading="## Per-section drafting",
    )
    queries = stub.captured["queries"]
    assert any("Per-section drafting" in q for q in queries)
    assert any("scope intersection" in q for q in queries)


class _SimpleStubKeywordIndex:
    """Minimal stub — returns no chunks, ignores all arguments."""

    def search(self, queries, *, k, files=None, per_file_quota=2):
        return []


async def test_draft_page_in_sections_returns_full_markdown():
    """Skeleton + per-section drafts + stitch returns full markdown."""
    spec = WikiPageSpec(title="Wiki Planner", purpose="...", files=["wiki_planner.py"])
    outline = PageOutline(
        sections=[
            SectionPlan(heading="Phase 1: Outline", kind="prose", focus="..."),
            SectionPlan(heading="Phase 2: File Selection", kind="prose", focus="..."),
        ],
        key_claims=["a", "b", "c"],
    )

    fast_llm = AsyncMock()
    # First call → build_skeleton; last call → stitch_sections
    skeleton = (
        "# Wiki Planner\n\n"
        "## Phase 1: Outline\n...\n\n"
        "## Phase 2: File Selection\n...\n"
    )
    stitched = (
        "# Wiki Planner\n\n"
        "## Phase 1: Outline\nSection 1 body.\n\n"
        "## Phase 2: File Selection\nSection 2 body.\n"
    )
    fast_llm.generate.side_effect = [skeleton, stitched]

    llm = AsyncMock()
    llm.generate.side_effect = ["Section 1 body.", "Section 2 body."]

    md = await draft_page_in_sections(
        spec=spec,
        outline=outline,
        keyword_index=_SimpleStubKeywordIndex(),
        llm=llm,
        fast_llm=fast_llm,
    )

    assert md.startswith("# Wiki Planner")
    assert "## Phase 1: Outline" in md
    assert "## Phase 2: File Selection" in md
    # Skeleton + Stitch on fast_llm; per-section on llm
    assert fast_llm.generate.call_count == 2
    assert llm.generate.call_count == 2


async def test_draft_page_in_sections_fails_loudly_on_retry_exhaustion():
    """No legacy fallback per spec §5.3 B2 — bubbles as WikiGenerationError."""
    from worker.pipeline.page.section_drafter import WikiGenerationError

    spec = WikiPageSpec(title="X", purpose="...", files=["x.py"])
    outline = PageOutline(
        sections=[SectionPlan(heading="Intro", kind="prose", focus="overview")],
        key_claims=["a", "b", "c"],
    )

    fast_llm = AsyncMock()
    fast_llm.generate.side_effect = RuntimeError("fast_llm explosion")

    llm = AsyncMock()
    llm.generate.return_value = "body"

    with pytest.raises(WikiGenerationError):
        await draft_page_in_sections(
            spec=spec,
            outline=outline,
            keyword_index=_SimpleStubKeywordIndex(),
            llm=llm,
            fast_llm=fast_llm,
        )
