# worker/pipeline/page/outline.py
"""Pass 1 of the multi-pass page generator — structured page outline.

Produces a JSON outline (sections, planned diagrams, key claims) that
guides the draft pass and targets the fact-check pass.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.pipeline_logging import log_final_failure, log_validation_retry
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

if TYPE_CHECKING:
    from worker.pipeline.planner.wiki_planner import WikiPageSpec

logger = logging.getLogger("worker.page_outline")

VALID_SECTION_KINDS = frozenset(
    {
        "prose",
        "prose+table",
        "prose+list",
        "prose+diagram",
        "prose+table+diagram",
    }
)

VALID_DIAGRAM_TYPES = frozenset(
    {
        "flowchart",
        "flowchart TD",
        "flowchart LR",
        "sequenceDiagram",
        "classDiagram",
        "stateDiagram-v2",
        "erDiagram",
        "journey",
        "gantt",
        "mindmap",
        "graph LR",
    }
)

_OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": sorted(VALID_SECTION_KINDS),
                    },
                    "focus": {"type": "string"},
                    "diagram": {
                        "type": ["object", "null"],
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": sorted(VALID_DIAGRAM_TYPES),
                            },
                            "purpose": {"type": "string"},
                            "source_files": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["heading", "kind", "focus"],
            },
        },
        "key_claims": {
            "type": "array",
            "items": {"type": "string"},
        },
        "out_of_scope_claims": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
    },
    "required": ["sections", "key_claims"],
}


@dataclass
class DiagramPlan:
    """Plan for a single diagram within a page section."""

    type: str
    purpose: str
    source_files: list[str] = field(default_factory=list)


@dataclass
class SectionPlan:
    """Plan for a single section within the page outline."""

    heading: str
    kind: str
    focus: str
    diagram: DiagramPlan | None = None


@dataclass
class PageOutline:
    """The validated outline for a single wiki page."""

    sections: list[SectionPlan]
    key_claims: list[str]
    out_of_scope_claims: list[str] = field(default_factory=list)


def validate_outline(
    raw: dict[str, Any],
    page_files: list[str],
) -> PageOutline:
    """Validate an LLM-produced outline dict and return a PageOutline.

    Raises:
        ValueError: On any validation failure with a descriptive message.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"Outline must be a dict, got {type(raw).__name__}")
    sections_raw = raw.get("sections", [])
    if not isinstance(sections_raw, list):
        raise ValueError(f"sections must be a list, got {type(sections_raw).__name__}")
    if not sections_raw:
        raise ValueError("Outline must have at least one section")

    claims = raw.get("key_claims", [])
    if not isinstance(claims, list):
        raise ValueError(f"key_claims must be a list, got {type(claims).__name__}")
    if len(claims) < 3 or len(claims) > 8:
        raise ValueError(f"key_claims must contain 3-8 items, got {len(claims)}")

    page_files_set = set(page_files)
    sections: list[SectionPlan] = []
    diagram_count = 0

    for s in sections_raw:
        if not isinstance(s, dict):
            raise ValueError(f"Each section must be a dict, got {type(s).__name__}")
        heading = s.get("heading", "")
        kind = s.get("kind", "")
        focus = s.get("focus", "")

        if not heading:
            raise ValueError("Section missing heading")
        if kind not in VALID_SECTION_KINDS:
            raise ValueError(
                f"Invalid section kind '{kind}' for '{heading}'. "
                f"Must be one of: {', '.join(sorted(VALID_SECTION_KINDS))}"
            )

        diagram = None
        diagram_raw = s.get("diagram")
        expects_diagram = "diagram" in kind
        if expects_diagram and not (isinstance(diagram_raw, dict) and diagram_raw):
            raise ValueError(f"Section '{heading}' requires a diagram object")
        if not expects_diagram and diagram_raw is not None:
            raise ValueError(f"Section '{heading}' must not include a diagram")
        if diagram_raw is not None:
            if not isinstance(diagram_raw, dict):
                raise ValueError(
                    f"diagram in section '{heading}' must be a dict, "
                    f"got {type(diagram_raw).__name__}"
                )
            diag_type = diagram_raw.get("type", "")
            if diag_type not in VALID_DIAGRAM_TYPES:
                raise ValueError(
                    f"Invalid diagram type '{diag_type}' in section '{heading}'. "
                    f"Must be one of: {', '.join(sorted(VALID_DIAGRAM_TYPES))}"
                )
            source_files = diagram_raw.get("source_files", [])
            if page_files_set and source_files:
                invalid = [f for f in source_files if f not in page_files_set]
                if invalid:
                    raise ValueError(
                        f"diagram source_files {invalid} not in page's assigned "
                        f"files for section '{heading}'"
                    )
            diagram = DiagramPlan(
                type=diag_type,
                purpose=diagram_raw.get("purpose", ""),
                source_files=source_files,
            )
            diagram_count += 1

        sections.append(
            SectionPlan(heading=heading, kind=kind, focus=focus, diagram=diagram)
        )

    # Minimum diagram count rule
    if len(sections) >= 3 and diagram_count < 2:
        raise ValueError(
            f"Pages with {len(sections)} sections must have at least 2 diagrams, "
            f"got {diagram_count}"
        )
    if diagram_count < 1:
        raise ValueError("Every page must produce at least 1 diagram")

    oos_raw = raw.get("out_of_scope_claims", [])
    if not isinstance(oos_raw, list):
        raise ValueError(
            f"out_of_scope_claims must be a list, got {type(oos_raw).__name__}"
        )
    oos_claims: list[str] = []
    for item in oos_raw:
        if not isinstance(item, str):
            raise ValueError(
                f"out_of_scope_claims items must be strings, got {type(item).__name__}"
            )
        oos_claims.append(item)

    return PageOutline(
        sections=sections, key_claims=claims, out_of_scope_claims=oos_claims
    )


_SYSTEM = (
    "You are a senior technical writer planning the structure of a wiki page. "
    "Given information about a software component — its files, entities, and "
    "dependencies — produce a structured JSON outline that will guide the "
    "actual page drafting.\n\n"
    "Rules:\n"
    "- 'kind' MUST be exactly one of: prose, prose+diagram, prose+list, "
    "prose+table, prose+table+diagram — these are format descriptors, not "
    "section topics\n"
    "- Choose diagram types that best fit the content from this palette:\n"
    "  flowchart, flowchart TD, flowchart LR, sequenceDiagram, classDiagram,\n"
    "  stateDiagram-v2, erDiagram, journey, gantt, mindmap, graph LR\n"
    "- diagram.source_files must be files assigned to THIS page\n"
    "- key_claims must be concrete, verifiable against source code\n"
    "- Output ONLY valid JSON"
)


def _build_outline_prompt(
    spec: WikiPageSpec,
    entity_summaries: str,
    dep_info: str | None,
    child_titles: list[str] | None = None,
    sibling_titles: list[str] | None = None,
    out_of_scope_topics: list[str] | None = None,
    signature_slices: dict[str, list[str]] | None = None,
) -> list[PromptSegment]:
    """Build the outline prompt with cacheable entity context."""
    # Cacheable prefix: entity summaries + dep info (reused by fact-check)
    cached_parts = [f"Page: {spec.title}\nPurpose: {spec.purpose}\n"]
    cached_parts.append(f"Assigned files: {', '.join(spec.files or [])}\n")
    cached_parts.append(f"Entity summaries:\n{entity_summaries}\n")
    if dep_info:
        cached_parts.append(f"Dependencies:\n{dep_info}\n")
    if child_titles:
        cached_parts.append(f"Child pages: {', '.join(child_titles)}\n")
    if sibling_titles:
        siblings_block = (
            "Sibling pages (DO NOT cover their topics; reference by name only):\n"
            + "\n".join(f"- {t}" for t in sibling_titles)
        )
        cached_parts.append(siblings_block + "\n")
    if out_of_scope_topics:
        oos_block = "Out-of-scope (covered elsewhere):\n" + "\n".join(
            f"- {t}" for t in out_of_scope_topics
        )
        cached_parts.append(oos_block + "\n")
    if signature_slices:
        slice_blocks: list[str] = []
        for path, slices in signature_slices.items():
            for s in slices:
                slice_blocks.append(f"### {path}\n```\n{s}\n```")
        if slice_blocks:
            cached_parts.append("Signature slices:\n" + "\n".join(slice_blocks) + "\n")

    # Variable tail: schema + instructions
    schema_json = json.dumps(_OUTLINE_SCHEMA, indent=2)
    n_sections = max(3, len(spec.files or []) // 2)
    min_diagrams = 2 if n_sections >= 3 else 1
    tail = (
        f"Create an outline with {n_sections}-{n_sections + 3} sections.\n"
        f"Include at least {min_diagrams} diagrams.\n"
        f"Provide 3-8 key_claims — concrete statements verifiable against source.\n\n"
        f"Output JSON matching this schema:\n{schema_json}"
    )

    return [
        PromptSegment(text="\n".join(cached_parts), cacheable=True),
        PromptSegment(text=tail),
    ]


async def generate_page_outline(
    spec: WikiPageSpec,
    entity_summaries: str,
    dep_info: str | None,
    fast_llm: LLMProvider,
    on_retry: OnRetryCallback | None = None,
    max_retries: int = 2,
    child_titles: list[str] | None = None,
    wiki_language: str = "en",
    sibling_titles: list[str] | None = None,
    out_of_scope_topics: list[str] | None = None,
    signature_slices: dict[str, list[str]] | None = None,
) -> PageOutline:
    """Generate and validate a page outline using the fast model.

    Retries up to *max_retries* times on validation failure, appending
    the error to the prompt.
    """
    from worker.pipeline.language import get_language_instruction

    segments = _build_outline_prompt(
        spec,
        entity_summaries,
        dep_info,
        child_titles,
        sibling_titles=sibling_titles,
        out_of_scope_topics=out_of_scope_topics,
        signature_slices=signature_slices,
    )
    system = _SYSTEM + get_language_instruction(wiki_language)

    for attempt in range(max_retries + 1):
        try:
            raw = await async_retry(
                fast_llm.generate_structured,
                segments,
                schema=_OUTLINE_SCHEMA,
                system=system,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
            return validate_outline(raw, page_files=spec.files or [])
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries:
                log_validation_retry(
                    logger,
                    stage="page_outline",
                    attempt=attempt + 1,
                    max_retries=max_retries + 1,
                    exc=e,
                    context={"page_title": spec.title, "files": len(spec.files or [])},
                )
                error_seg = PromptSegment(
                    text=f"\n\nPrevious attempt failed: {e}. Fix and retry."
                )
                segments = list(segments) + [error_seg]
            else:
                log_final_failure(
                    logger,
                    stage="page_outline",
                    exc=e,
                    context={"page_title": spec.title, "files": len(spec.files or [])},
                )
                raise
        except Exception as e:
            log_final_failure(
                logger,
                stage="page_outline",
                exc=e,
                context={"page_title": spec.title, "files": len(spec.files or [])},
            )
            raise
