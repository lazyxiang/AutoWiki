# worker/pipeline/page_draft.py
"""Pass 2 of the multi-pass page generator — draft generation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.page_generator import (
    PageResult,
    _format_context_chunks,
    _format_entity_details,
)
from worker.pipeline.page_outline import PageOutline
from worker.utils.mermaid import sanitize_mermaid_blocks
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

if TYPE_CHECKING:
    from worker.pipeline.wiki_planner import WikiPageSpec

DRAFT_SYSTEM = (
    "You are a senior technical writer creating comprehensive, "
    "production-quality wiki documentation for a software repository.\n\n"
    "Source of truth: the source code provided below is canonical. Any "
    "documentation excerpt (files ending in .md, .rst, etc.) may be out "
    "of date and must be treated as a hint, not a fact. When documentation "
    "and code disagree, trust the code. Never cite a documentation file as "
    "the source of a technical claim — cite the code file that actually "
    "implements the behavior.\n\n"
    "Rules:\n"
    "- Every technical claim MUST be grounded in the provided source code\n"
    "- Never embed fenced code blocks (no ```python, ```js, etc.). The ONLY "
    "fenced blocks allowed are ```mermaid for diagrams\n"
    "- Short inline identifiers like `ClassName.method()` or `MAX_RETRIES = 3` "
    "ARE permitted — these describe the API surface, not code excerpts\n"
    "- After each major section or subsection, add a source annotation in "
    "italics: *Source: path/to/file.py:10-45*\n"
    "- Use tables and bulleted lists for enumerating options, fields, parameters, "
    "or comparisons. Use prose for narrative and design rationale.\n"
    "- Choose diagram types that best fit the content from this palette:\n"
    "  flowchart TD, flowchart LR, sequenceDiagram, classDiagram,\n"
    "  stateDiagram-v2, erDiagram, journey, gantt, mindmap, graph LR\n"
    "- Every diagram MUST be preceded by: **Diagram: <one-line header>**\n"
    "  and followed by: *Source: <file_path>:<start_line>-<end_line>*\n"
    "- IMPORTANT Mermaid quoting rules — violating these causes parse errors:\n"
    '  - Node labels with special chars: A["Server (HTTP)"] not A[Server (HTTP)]\n'
    '  - Edge labels with special chars: -->|"GET /api/{id}"| not -->|GET /api/{id}|\n'
    "  - Special characters requiring quotes: ( ) { } | < > /\n"
    "- Write for developers who are new to this codebase but experienced programmers\n"
    "- Organize content from high-level concepts down to implementation details"
)


def build_draft_prompt(
    spec: WikiPageSpec,
    outline: PageOutline,
    context_chunks: list[dict],
    repo_name: str,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
    child_contents: list[PageResult] | None = None,
) -> list[PromptSegment]:
    """Build the draft prompt as a list of PromptSegments with caching."""
    # ── Cacheable prefix: source context ──
    cached_parts = [f"Repository: {repo_name}\nPage: {spec.title}\n"]

    if spec.purpose:
        cached_parts.append(f"Purpose: {spec.purpose}\n")

    cached_parts.append(f"Source files: {', '.join(spec.files or [])}\n")

    if dep_info:
        deps_on = dep_info.get("depends_on", [])
        deps_by = dep_info.get("depended_by", [])
        ext_deps = dep_info.get("external_deps", [])
        dep_lines = []
        if deps_on:
            dep_lines.append(f"- Depends on: {', '.join(deps_on)}")
        if deps_by:
            dep_lines.append(f"- Depended on by: {', '.join(deps_by)}")
        if ext_deps:
            dep_lines.append(f"- External dependencies: {', '.join(ext_deps[:10])}")
        if dep_lines:
            cached_parts.append("Dependencies:\n" + "\n".join(dep_lines) + "\n")

    if entity_details:
        cached_parts.append(
            f"Key entities:\n{_format_entity_details(entity_details)}\n"
        )

    context = _format_context_chunks(context_chunks)
    cached_parts.append(
        f"Relevant source code (with file paths and line numbers):\n{context}\n"
    )

    if child_contents:
        child_sections = []
        for child in child_contents:
            child_sections.append(f'### Child: "{child.title}"\n{child.content}')
        cached_parts.append(
            "## Child Pages (already generated)\n"
            "The following child pages have been written. Your role is to "
            "SYNTHESIZE and CONNECT — provide the high-level narrative, "
            "explain how these components relate, and add context that "
            "individual pages cannot provide. Do NOT repeat details covered "
            "in child pages; reference them instead.\n\n"
            + "\n\n".join(child_sections)
            + "\n"
        )

    # ── Variable tail: outline + instructions ──
    outline_json = json.dumps(
        {
            "sections": [
                {
                    "heading": s.heading,
                    "kind": s.kind,
                    "focus": s.focus,
                    "diagram": (
                        {
                            "type": s.diagram.type,
                            "purpose": s.diagram.purpose,
                            "source_files": s.diagram.source_files,
                        }
                        if s.diagram
                        else None
                    ),
                }
                for s in outline.sections
            ],
        },
        indent=2,
    )

    has_children = bool(child_contents)
    if has_children:
        instruction = (
            f'Write a wiki page for "{spec.title}" that serves as the entry point '
            "for its child pages. Follow the outline below exactly.\n"
            "Do NOT duplicate content from child pages — reference them by name.\n"
        )
    else:
        instruction = (
            f'Write a comprehensive wiki page for "{spec.title}". '
            "Follow the outline below exactly.\n"
        )

    tail = (
        f"{instruction}\n"
        f"Page outline:\n{outline_json}\n\n"
        "For each section, write the content matching its 'kind':\n"
        "- prose: narrative paragraphs\n"
        "- prose+table: narrative with a summarizing table\n"
        "- prose+list: narrative with a bulleted list\n"
        "- prose+diagram: narrative with a Mermaid diagram matching the planned type\n"
        "- prose+table+diagram: narrative with both table and diagram\n\n"
        "Output Markdown only."
    )

    return [
        PromptSegment(text="\n".join(cached_parts), cacheable=True),
        PromptSegment(text=tail),
    ]


async def generate_draft(
    spec: WikiPageSpec,
    outline: PageOutline,
    context_chunks: list[dict],
    repo_name: str,
    llm: LLMProvider,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
    child_contents: list[PageResult] | None = None,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
) -> str:
    """Generate the draft Markdown for a wiki page using the main model."""
    from worker.pipeline.language import get_language_instruction

    segments = build_draft_prompt(
        spec=spec,
        outline=outline,
        context_chunks=context_chunks,
        repo_name=repo_name,
        dep_info=dep_info,
        entity_details=entity_details,
        child_contents=child_contents,
    )
    system = DRAFT_SYSTEM + get_language_instruction(wiki_language)

    content = await async_retry(
        llm.generate,
        segments,
        system=system,
        transient_exceptions=TRANSIENT_EXCEPTIONS,
        on_retry=on_retry,
    )

    return sanitize_mermaid_blocks(content)
