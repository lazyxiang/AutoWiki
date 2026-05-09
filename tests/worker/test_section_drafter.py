"""Tests for worker.pipeline.page.section_drafter — Pass 2a (Skeleton)."""

from unittest.mock import AsyncMock

from worker.pipeline.page.outline import PageOutline, SectionPlan
from worker.pipeline.page.section_drafter import build_skeleton


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
