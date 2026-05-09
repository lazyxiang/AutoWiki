"""Section-level wiki page drafting (Pass 2a/2b/2c).

Replaces the single-shot Pass 2 draft with three sub-passes:

* Pass 2a — :func:`build_skeleton` produces a Markdown frame (H1 + H2 headings
  + one-line purposes) from the outline. Fast model.
* Pass 2b — :func:`draft_section` (added in B2.3) drafts each section body
  independently with section-scoped retrieval.
* Pass 2c — :func:`stitch_sections` (added in B2.4) reconciles the drafts.

Per spec §5.3 B2 / plan task B2.2.
"""

from __future__ import annotations

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.page.outline import PageOutline

_SKELETON_SYSTEM = (
    "You are drafting the rendered shape of a wiki page from its outline. "
    "Output Markdown only: a single H1 with the page title, then H2 headings "
    "for each section in order, with a one-sentence purpose line under each "
    "heading. Do not write body text."
)


async def build_skeleton(
    *,
    title: str,
    outline: PageOutline,
    fast_llm: LLMProvider,
) -> str:
    """Pass 2a — produce a Markdown skeleton (H1 + H2 + per-section purpose).

    The skeleton fixes heading wording, ordering, and rendered shape so the
    per-section drafter (B2.3) can fill bodies into a stable frame. Fast model
    is sufficient — no source code is consumed at this stage.
    """
    user_lines = [f"# {title}", "", "Sections:"]
    user_lines.extend(f"- {s.heading}: {s.focus}" for s in outline.sections)
    user_text = "\n".join(user_lines)

    return await fast_llm.generate(
        [
            PromptSegment(text=_SKELETON_SYSTEM, cacheable=True),
            PromptSegment(text=user_text),
        ]
    )
