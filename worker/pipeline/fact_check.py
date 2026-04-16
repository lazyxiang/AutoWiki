# worker/pipeline/fact_check.py
"""Pass 3 (fact-check) and Pass 4 (targeted revision) of the wiki page generator."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.page_outline import PageOutline
from worker.pipeline.pipeline_logging import log_final_failure
from worker.utils.mermaid import sanitize_mermaid_blocks
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

logger = logging.getLogger("worker.fact_check")

_FACT_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["claim", "diagram"]},
                    "claim": {"type": "string"},
                    "diagram_index": {"type": "integer", "minimum": 0},
                    "section": {"type": "string"},
                    "reason": {"type": "string"},
                    "suggested_fix": {"type": "string"},
                },
                "required": ["kind", "section", "reason", "suggested_fix"],
                # kind-specific required fields: "claim" issues must have "claim",
                # "diagram" issues must have "diagram_index"
                "if": {
                    "properties": {"kind": {"const": "claim"}},
                    "required": ["kind"],
                },
                "then": {"required": ["claim"]},
                "else": {"required": ["diagram_index"]},
            },
        },
    },
    "required": ["verdict", "issues"],
}


@dataclass
class FactCheckIssue:
    kind: str  # "claim" or "diagram"
    section: str
    reason: str
    suggested_fix: str
    claim: str | None = None
    diagram_index: int | None = None


@dataclass
class FactCheckResult:
    verdict: str  # "pass" or "fail"
    issues: list[FactCheckIssue] = field(default_factory=list)


def parse_fact_check_result(raw: dict[str, Any]) -> FactCheckResult:
    verdict = raw.get("verdict", "pass")
    issues = []
    for issue_raw in raw.get("issues", []):
        issues.append(
            FactCheckIssue(
                kind=issue_raw.get("kind", "claim"),
                section=issue_raw.get("section", ""),
                reason=issue_raw.get("reason", ""),
                suggested_fix=issue_raw.get("suggested_fix", ""),
                claim=issue_raw.get("claim"),
                diagram_index=issue_raw.get("diagram_index"),
            )
        )
    return FactCheckResult(verdict=verdict, issues=issues)


_FACT_CHECK_SYSTEM = (
    "You are a technical accuracy reviewer. Your job is to verify claims "
    "and diagrams in a wiki page draft against the actual source code. "
    "You must be precise: only flag issues where the draft demonstrably "
    "contradicts or is unsupported by the source code provided.\n\n"
    "Output ONLY valid JSON."
)


def _build_fact_check_prompt(
    draft: str,
    outline: PageOutline,
    entity_summaries: str,
    dep_info: str | None,
    targeted_chunks: str,
) -> tuple[list[PromptSegment], PromptSegment]:
    """Return ``(system_segments, user_segment)`` for the fact-check call.

    The reusable entity/dependency prefix is returned as a cacheable *system*
    segment so Anthropic's ``generate_structured`` can apply ``cache_control``
    on the system turn.  (The user turn is intentionally flattened to plain
    text for structured calls, so cacheable markers there have no effect.)
    """
    cached_parts = [f"Entity summaries:\n{entity_summaries}\n"]
    if dep_info:
        cached_parts.append(f"Dependencies:\n{dep_info}\n")

    claims_json = json.dumps(outline.key_claims, indent=2)

    diagrams = []
    for s in outline.sections:
        if s.diagram:
            diagrams.append(
                {
                    # index is the per-section mermaid block ordinal (0-based).
                    # Each section has at most one planned diagram, so this is
                    # always 0.  The revision/strip logic uses this value to
                    # locate the correct block within the section.
                    "index": 0,
                    "section": s.heading,
                    "type": s.diagram.type,
                    "purpose": s.diagram.purpose,
                    "source_files": s.diagram.source_files,
                }
            )
    diagrams_json = json.dumps(diagrams, indent=2)
    schema_json = json.dumps(_FACT_CHECK_SCHEMA, indent=2)

    tail = (
        f"## Draft to verify:\n{draft}\n\n"
        f"## Key claims to verify:\n{claims_json}\n\n"
        f"## Diagrams to verify:\n{diagrams_json}\n\n"
        f"## Relevant source code:\n{targeted_chunks}\n\n"
        "For each claim, check if the source code supports it. "
        "For each diagram, check if the relationships depicted exist in the code. "
        "Only flag issues that are clearly wrong — do not flag stylistic concerns.\n\n"
        f"Output JSON matching this schema:\n{schema_json}"
    )

    system_segments = [PromptSegment(text="\n".join(cached_parts), cacheable=True)]
    return system_segments, PromptSegment(text=tail)


async def run_fact_check(
    draft: str,
    outline: PageOutline,
    entity_summaries: str,
    dep_info: str | None,
    targeted_chunks: str,
    fast_llm: LLMProvider,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
) -> FactCheckResult:
    """Run fact-check on a draft using the fast model. Fails open on error."""
    from worker.pipeline.language import get_language_instruction

    context_segments, user_segment = _build_fact_check_prompt(
        draft, outline, entity_summaries, dep_info, targeted_chunks
    )
    # Place the static instruction first, then the cacheable entity/dep context.
    # Keeping both in the system turn ensures Anthropic's generate_structured
    # can apply cache_control (the user turn is flattened for structured calls).
    lang = get_language_instruction(wiki_language)
    system_segments: list[PromptSegment] = [
        PromptSegment(text=_FACT_CHECK_SYSTEM + lang),
        *context_segments,
    ]

    try:
        raw = await async_retry(
            fast_llm.generate_structured,
            [user_segment],
            schema=_FACT_CHECK_SCHEMA,
            system=system_segments,
            transient_exceptions=TRANSIENT_EXCEPTIONS,
            on_retry=on_retry,
        )
    except Exception as exc:
        log_final_failure(
            logger,
            stage="fact_check",
            exc=exc,
            context={
                "outline_sections": len(outline.sections),
                "key_claims": len(outline.key_claims),
            },
        )
        return FactCheckResult(verdict="pass")

    return parse_fact_check_result(raw)


def _find_section_start(draft: str, section_header: str) -> int:
    """Return the position of an anchored Markdown heading for *section_header*.

    Accepts *section_header* with or without a leading ``##`` prefix (e.g.
    both ``"## Flow"`` and ``"Flow"`` match ``## Flow`` in the draft).
    Occurrences of the heading text inside prose or code fences are ignored.
    Returns -1 when no heading is found.
    """
    heading_text = re.sub(r"^#+\s*", "", section_header).strip()
    pattern = re.compile(rf"^#+\s+{re.escape(heading_text)}\s*$", re.MULTILINE)
    match = pattern.search(draft)
    return match.start() if match else -1


def _section_bounds(draft: str, section_header: str) -> tuple[int, int]:
    """Return ``(section_start, section_end)`` for *section_header*.

    Uses an anchored heading match so prose occurrences of the same words are
    ignored.  *section_start* is the position of the ``##`` character;
    *section_end* is the start of the next ``##``-level heading (or end of
    string).  Returns ``(-1, -1)`` when the section is not found.
    """
    section_start = _find_section_start(draft, section_header)
    if section_start == -1:
        return -1, -1
    heading_end = draft.find("\n", section_start)
    heading_end = heading_end + 1 if heading_end != -1 else len(draft)
    next_section = re.search(r"^## ", draft[heading_end:], re.MULTILINE)
    section_end = heading_end + next_section.start() if next_section else len(draft)
    return section_start, section_end


def strip_failed_claim(
    draft: str, claim: str, reason: str, section: str | None = None
) -> str:
    """Remove sentences containing the claim text.

    If *section* is provided, removal is scoped to that section only so that
    identical phrases in other sections are left untouched.
    Returns draft unchanged if the claim or section is not found.
    """
    claim_lower = claim.lower()

    def _strip_lines(text: str) -> str:
        lines = text.split("\n")
        result_lines = []
        for line in lines:
            if claim_lower in line.lower():
                sentences = re.split(r"(?<=[.!?])\s+", line)
                kept = []
                removed = False
                for sentence in sentences:
                    if claim_lower in sentence.lower():
                        removed = True
                    else:
                        kept.append(sentence)
                if removed:
                    replacement = " ".join(kept)
                    if replacement:
                        result_lines.append(replacement)
                    result_lines.append(f"<!-- removed: {reason} -->")
                else:
                    result_lines.append(line)
            else:
                result_lines.append(line)
        return "\n".join(result_lines)

    if section:
        sec_start, sec_end = _section_bounds(draft, section.strip())
        if sec_start == -1:
            return draft
        return (
            draft[:sec_start] + _strip_lines(draft[sec_start:sec_end]) + draft[sec_end:]
        )

    return _strip_lines(draft)


def strip_failed_diagram(
    draft: str, section: str, diagram_index: int, reason: str
) -> str:
    """Remove a specific mermaid block and its header/source from the draft."""
    section_start, section_end = _section_bounds(draft, section.strip())
    if section_start == -1:
        return draft

    section_text = draft[section_start:section_end]

    mermaid_pattern = re.compile(
        r"(\*\*Diagram:[^\n]*\*\*\s*\n\s*\n)?"
        r"(```mermaid\n.*?```)"
        r"(\s*\n\s*\*Source:[^\n]*\*)?",
        re.DOTALL,
    )
    matches = list(mermaid_pattern.finditer(section_text))

    if diagram_index < len(matches):
        match = matches[diagram_index]
        replacement = f"<!-- diagram removed: {reason} -->"
        new_section = (
            section_text[: match.start()] + replacement + section_text[match.end() :]
        )
        draft = draft[:section_start] + new_section + draft[section_end:]

    return draft


_REVISION_SYSTEM = (
    "You are a technical writer revising a wiki page to fix specific factual "
    "errors. Revise ONLY the sections mentioned in the issues — leave "
    "everything else EXACTLY as-is, character for character.\n"
    "Do NOT include any reasoning, preamble, meta-commentary, or explanation. "
    "Begin your response directly with the Markdown content, starting with the "
    "page title heading (e.g. # Page Title).\n"
    "Keep the total revised page within 2,000–6,000 words; do not expand "
    "sections that are not listed in the issues.\n"
    "Output the complete revised Markdown page."
)

_DIAGRAM_REVISION_SYSTEM = (
    "You are a technical writer fixing a specific Mermaid diagram. "
    "Output ONLY the corrected ```mermaid block, nothing else."
)


async def run_targeted_revision(
    draft: str,
    issues: list[FactCheckIssue],
    context_segments: list[PromptSegment],
    llm: LLMProvider,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
) -> str:
    """Apply targeted revision to fix fact-check issues."""
    from worker.pipeline.language import get_language_instruction

    claim_issues = [i for i in issues if i.kind == "claim"]
    diagram_issues = [i for i in issues if i.kind == "diagram"]

    revised = draft

    if claim_issues:
        issues_json = json.dumps(
            [
                {
                    "claim": i.claim,
                    "section": i.section,
                    "reason": i.reason,
                    "suggested_fix": i.suggested_fix,
                }
                for i in claim_issues
            ],
            indent=2,
        )
        tail = (
            f"## Current draft:\n{revised}\n\n"
            f"## Issues to fix:\n{issues_json}\n\n"
            "Revise only the sections containing these issues. "
            "Leave every other section verbatim.\n"
            "Output the complete revised Markdown."
        )
        revision_segments = list(context_segments) + [PromptSegment(text=tail)]
        system = _REVISION_SYSTEM + get_language_instruction(wiki_language)

        revised = await async_retry(
            llm.generate,
            revision_segments,
            system=system,
            transient_exceptions=TRANSIENT_EXCEPTIONS,
            on_retry=on_retry,
        )
        revised = sanitize_mermaid_blocks(revised)

    for diag_issue in diagram_issues:
        mermaid_pattern = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
        section_header = diag_issue.section.strip()
        sec_start, sec_end = _section_bounds(revised, section_header)
        if sec_start == -1:
            continue

        section_text = revised[sec_start:sec_end]
        matches = list(mermaid_pattern.finditer(section_text))
        idx = diag_issue.diagram_index if diag_issue.diagram_index is not None else 0
        if idx >= len(matches):
            continue

        original_block = matches[idx].group(0)
        tail = (
            f"Section: {section_header}\n"
            f"Current diagram:\n{original_block}\n\n"
            f"Issue: {diag_issue.reason}\n"
            f"Suggested fix: {diag_issue.suggested_fix}\n\n"
            "Output ONLY the corrected ```mermaid block."
        )
        diag_segments = list(context_segments) + [PromptSegment(text=tail)]
        system = _DIAGRAM_REVISION_SYSTEM + get_language_instruction(wiki_language)

        corrected = await async_retry(
            llm.generate,
            diag_segments,
            system=system,
            transient_exceptions=TRANSIENT_EXCEPTIONS,
            on_retry=on_retry,
        )
        corrected = sanitize_mermaid_blocks(corrected)

        if "```mermaid" not in corrected:
            corrected = f"```mermaid\n{corrected}\n```"

        abs_start = sec_start + matches[idx].start()
        abs_end = sec_start + matches[idx].end()
        revised = revised[:abs_start] + corrected + revised[abs_end:]

    return revised
