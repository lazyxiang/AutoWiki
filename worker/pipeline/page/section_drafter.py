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

from typing import TYPE_CHECKING

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.page.outline import PageOutline, SectionPlan
from worker.pipeline.retrieval.chunk import Chunk

if TYPE_CHECKING:
    from worker.pipeline.retrieval.keyword_index import KeywordIndex

_SKELETON_SYSTEM = (
    "You are drafting the rendered shape of a wiki page from its outline. "
    "Output Markdown only: a single H1 with the page title, then H2 headings "
    "for each section in order, with a one-sentence purpose line under each "
    "heading. Do not write body text."
)

_SECTION_DRAFT_SYSTEM = (
    "You are drafting one section of a wiki page. Write Markdown body text only — "
    "do NOT repeat the H2 heading; the heading is already in place. Stay strictly "
    "within the section's focus; do not cover topics handled by other sections. "
    "Cite specific functions / classes / files when making concrete claims."
)


def _format_chunks_for_section(chunks: list[Chunk]) -> str:
    """Format retrieved Chunk objects into a plain text block for the section prompt."""
    if not chunks:
        return "No source chunks retrieved."
    parts = []
    for c in chunks:
        header = f"{c.file}:{c.line_start}-{c.line_end}"
        parts.append(f"{header}\n{c.text}\n---")
    return "\n".join(parts)


def _build_section_prompt(
    *,
    section: SectionPlan,
    chunks: list[Chunk],
    skeleton_heading: str,
) -> list[PromptSegment]:
    """Build the per-section draft prompt as a list of PromptSegments.

    Returns a cacheable system segment with drafting instructions and a
    non-cacheable user segment with the heading, focus, and retrieved chunks.
    """
    chunk_text = _format_chunks_for_section(chunks)
    user_text = (
        f"Section heading: {skeleton_heading}\n"
        f"Section focus: {section.focus}\n\n"
        f"Retrieved source chunks:\n{chunk_text}\n\n"
        "Write the body of this section now."
    )
    return [
        PromptSegment(text=_SECTION_DRAFT_SYSTEM, cacheable=True),
        PromptSegment(text=user_text),
    ]


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


async def draft_section(
    *,
    section: SectionPlan,
    spec_files: list[str],
    keyword_index: KeywordIndex,
    llm: LLMProvider,
    skeleton_heading: str,
) -> str:
    """Pass 2b — draft a single section's body with section-scoped retrieval.

    Section scope = ``spec_files`` ∩ ``section.diagram.source_files`` (when the
    section has a diagram), else ``spec_files``. This is the lever that closes
    the orchestrator-under-coverage gap from spec §3 P2 / §5.3 B2: each section
    retrieves chunks from its own scope, so an orchestrator-only section gets
    orchestrator chunks regardless of how dense the outline file is.

    Args:
        section: The planned section from the page outline.
        spec_files: All files assigned to this page (the page-level scope).
        keyword_index: BM25 keyword index used to retrieve relevant source chunks.
        llm: LLM provider used to generate the section body.
        skeleton_heading: The exact H2 heading string from the skeleton (e.g.
            ``"## Phase 2: File Selection"``).

    Returns:
        Markdown body text for this section (heading not included).
    """
    queries = [section.heading]
    if section.focus:
        queries.append(section.focus)

    diagram_files = section.diagram.source_files if section.diagram else []
    section_scope = list(set(spec_files) & set(diagram_files)) or list(spec_files)

    chunks = keyword_index.search(
        [q for q in queries if q.strip()],
        k=8,
        files=section_scope,
        per_file_quota=2,
    )

    prompt = _build_section_prompt(
        section=section,
        chunks=chunks,
        skeleton_heading=skeleton_heading,
    )
    return await llm.generate(prompt)
