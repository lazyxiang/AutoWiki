# worker/pipeline/page/fact_check.py
"""Pass 3 (fact-check) and Pass 4 (targeted revision) of the wiki page generator."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.language import get_language_instruction
from worker.pipeline.page.outline import PageOutline
from worker.pipeline.pipeline_logging import log_final_failure
from worker.utils.mermaid import sanitize_mermaid_blocks
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

logger = logging.getLogger("worker.fact_check")

# Sentinel prefix used to tag synthetic out-of-scope issues so the generator
# can identify and strip them before handing remaining issues to the LLM revision.
# Using a machine-tagged prefix that the LLM cannot organically produce prevents
# misclassification of LLM-generated reasons that happen to start with natural
# language like "out of scope for this component".
OUT_OF_SCOPE_REASON_PREFIX = "autowiki:oos:"

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
                # Note: Anthropic's structured-output schema does not honour
                # JSON Schema ``if``/``then``/``else``, so kind-specific
                # required fields (``claim`` for "claim" issues,
                # ``diagram_index`` for "diagram" issues) are enforced at
                # parse time in :func:`parse_fact_check_result`.
                "required": ["kind", "section", "reason", "suggested_fix"],
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
    """Parse an LLM-produced fact-check payload, dropping malformed issues.

    Centralizes kind-specific contract enforcement that JSON Schema
    ``if``/``then``/``else`` cannot reliably express in the Anthropic
    structured-output API:

    * ``kind == "claim"`` requires a non-empty ``claim`` string.
    * ``kind == "diagram"`` requires a non-negative integer ``diagram_index``.

    Issues that fail these checks (or carry an unknown ``kind``) are skipped
    so downstream callers never have to re-validate.
    """

    verdict = raw.get("verdict", "pass")
    issues: list[FactCheckIssue] = []
    for issue_raw in raw.get("issues", []):
        kind = issue_raw.get("kind", "claim")
        section = issue_raw.get("section", "")
        reason = issue_raw.get("reason", "")
        suggested_fix = issue_raw.get("suggested_fix", "")
        claim = issue_raw.get("claim")
        diagram_index = issue_raw.get("diagram_index")

        if kind == "claim":
            if not isinstance(claim, str) or not claim.strip():
                logger.debug(
                    "fact_check: dropping 'claim' issue with empty/missing claim"
                )
                continue
        elif kind == "diagram":
            if not isinstance(diagram_index, int) or diagram_index < 0:
                logger.debug(
                    "fact_check: dropping 'diagram' issue with invalid index=%r",
                    diagram_index,
                )
                continue
        else:
            logger.debug("fact_check: dropping issue with unknown kind=%r", kind)
            continue

        issues.append(
            FactCheckIssue(
                kind=kind,
                section=section,
                reason=reason,
                suggested_fix=suggested_fix,
                claim=claim,
                diagram_index=diagram_index,
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


def _normalize_text(text: str) -> str:
    """Collapse internal whitespace for substring matching."""
    return re.sub(r"\s+", " ", text).strip()


def _find_oos_issues(draft: str, outline: PageOutline) -> list[FactCheckIssue]:
    """Return synthetic FactCheckIssue items for out-of-scope claims found in draft.

    Each issue has kind="claim" and reason starting with OUT_OF_SCOPE_REASON_PREFIX
    so the generator can identify and strip them pre-revision.

    The normalized form of each claim (whitespace-collapsed) is stored on the issue
    so that :func:`strip_failed_claim` can reliably find it in the un-normalized draft.
    """
    if not outline.out_of_scope_claims:
        return []

    draft_norm = _normalize_text(draft)
    issues: list[FactCheckIssue] = []
    # Deduplicate while preserving order so the same OOS phrase is not stripped twice.
    seen: dict[str, None] = {}
    for oos_claim in outline.out_of_scope_claims:
        claim_norm = _normalize_text(oos_claim)
        if not claim_norm or claim_norm in seen:
            continue
        seen[claim_norm] = None
        if re.search(re.escape(claim_norm), draft_norm, re.IGNORECASE):
            issues.append(
                FactCheckIssue(
                    kind="claim",
                    section="",
                    # Store the normalized form so strip_failed_claim can match it
                    # against whitespace-collapsed lines without relying on the raw
                    # (potentially multi-space) LLM output string.
                    claim=claim_norm,
                    reason=(
                        f"{OUT_OF_SCOPE_REASON_PREFIX} (covered by sibling page): "
                        f"{oos_claim[:200]}"
                    ),
                    suggested_fix=(
                        "Remove this sentence; refer to the sibling page by title."
                    ),
                )
            )
    return issues


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
    """Run fact-check on a draft using the fast model. Fails open on error.

    If the draft contains any out-of-scope claims (from
    ``outline.out_of_scope_claims``), returns early with ``verdict="fail"`` and
    synthetic issues — the LLM is NOT called.

    **Early-exit semantics**: when ``outline.out_of_scope_claims`` produces any
    hit on ``draft``, this function returns immediately with ``verdict="fail"``
    and synthetic OOS issues.  The LLM fact-check call is skipped entirely.
    The rationale is that out-of-scope content must be removed before any other
    fact-check signal is meaningful; the generator strips OOS sentences
    deterministically (no LLM needed) and then proceeds.  Note that the
    generator does NOT re-run fact-check after stripping, so genuine factual
    errors in the remaining prose are not caught in that pass.  Per spec §5.3
    B3 / plan B2.1 step 3.
    """
    # ── Early-exit: out-of-scope claim gate ──
    oos_issues = _find_oos_issues(draft, outline)
    if oos_issues:
        logger.debug(
            "fact_check: early-exit with %d out-of-scope hit(s)", len(oos_issues)
        )
        return FactCheckResult(verdict="fail", issues=oos_issues)

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
    pattern = re.compile(rf"^##\s+{re.escape(heading_text)}\s*$", re.MULTILINE)
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


def _safe_comment_text(text: str) -> str:
    """Neutralise HTML-comment terminator sequences in *text*.

    The fact-check ``reason`` is interpolated into ``<!-- removed: ... -->`` /
    ``<!-- diagram removed: ... -->`` markers.  An LLM-produced ``-->`` (or
    even a stray ``--``) would close the comment early and leak content into
    the rendered Markdown.  Replacing both sequences is enough: ``-->`` is
    matched first (because ``--`` is a substring), and the replacement
    ``--&gt;`` cannot accidentally re-form a terminator after the second
    pass.
    """

    return text.replace("-->", "--&gt;").replace("--", "—")


def _trim_diagram_revision_metadata(corrected: str) -> str:
    lines = corrected.strip().splitlines()
    while lines and re.match(r"^\*{0,2}Diagram:.*\*{0,2}\s*$", lines[0].strip()):
        lines.pop(0)
    while lines and re.match(r"^\*{0,2}Source:.*\*{0,2}\s*$", lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


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
            # Normalize internal whitespace before matching so that OOS claims
            # stored in their normalized form (single spaces) still find their
            # counterpart in lines that may have irregular spacing from LLM output.
            line_norm_lower = re.sub(r"\s+", " ", line.lower())
            if claim_lower in line_norm_lower:
                sentences = re.split(r"(?<=[.!?])\s+", line)
                kept = []
                removed = False
                for sentence in sentences:
                    if claim_lower in re.sub(r"\s+", " ", sentence.lower()):
                        removed = True
                    else:
                        kept.append(sentence)
                if removed:
                    replacement = " ".join(kept)
                    if replacement:
                        result_lines.append(replacement)
                    result_lines.append(
                        f"<!-- removed: {_safe_comment_text(reason)} -->"
                    )
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
        replacement = f"<!-- diagram removed: {_safe_comment_text(reason)} -->"
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
    "Output the complete revised Markdown page.\n"
    "Never embed fenced code blocks (no ```python, ```js, etc.). The ONLY "
    "fenced blocks allowed are ```mermaid for diagrams.\n"
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
        corrected = _trim_diagram_revision_metadata(corrected)

        if "```mermaid" not in corrected:
            corrected = f"```mermaid\n{corrected}\n```"

        abs_start = sec_start + matches[idx].start()
        abs_end = sec_start + matches[idx].end()
        revised = revised[:abs_start] + corrected + revised[abs_end:]

    return revised
