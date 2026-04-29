"""Stage 6 of the generation pipeline — multi-pass wiki page orchestrator.

Each :class:`~worker.pipeline.wiki_planner.WikiPageSpec` is processed through
a 4-pass pipeline:

1. **Outline** (fast model) — produces a structured ``PageOutline`` with
   sections, planned diagrams, and key claims to verify.
2. **Draft** (main model) — generates full Markdown from the outline using
   multi-query RAG context.
3. **Fact-check** (fast model) — verifies key claims and diagrams against the
   source code; returns a :class:`~worker.pipeline.fact_check.FactCheckResult`.
4. **Revision** (main model, conditional) — applies targeted fixes when the
   fact-check verdict is ``"fail"``; falls back to deterministic claim/diagram
   stripping for any still-flagged issues.

Post-processing: :func:`~worker.pipeline.diagram_post_processor.ensure_diagram_headers`
and :func:`~worker.utils.mermaid.sanitize_mermaid_blocks` are applied to the
final draft.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from worker.embedding.base import EmbeddingProvider
from worker.llm.base import LLMProvider
from worker.pipeline.page_formatters import (
    _format_context_chunks,
    _format_entity_details,
)
from worker.pipeline.rag_indexer import FAISSStore
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

if TYPE_CHECKING:
    from worker.pipeline.ast_analysis import FileAnalysis
    from worker.pipeline.dependency_graph import DependencyGraph


logger = logging.getLogger("worker.page_generator")

_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
PageProgressCallback = Callable[[str, str | None], Awaitable[None]]


def _append_source_files_table(content: str, files: list[str]) -> str:
    """Append a Source Files table at the end of the page.

    Skipped when *files* is empty. The table lists every source file assigned
    to the page spec so readers can navigate directly to the implementation.
    """
    if not files:
        return content

    rows = "\n".join(f"| `{f}` |" for f in files)
    table = f"\n\n## Source Files\n\n| File |\n|------|\n{rows}"
    return content.rstrip() + table


def _strip_code_blocks(draft: str) -> str:
    """Strip fenced code blocks that are not Mermaid diagrams.

    Scans the draft line by line.  When a fenced opening that is *not*
    ``mermaid`` is encountered the opening fence line and its matching closing
    fence line are removed; the content lines between them are kept so that
    readers still see the information (just without the code-block framing).

    ``````mermaid`` blocks are left completely untouched.
    """
    # Matches optional leading spaces (≤3), ``` marker, optional language tag,
    # and any trailing info string (e.g. ```python title="x").
    _FENCE_OPEN_RE = re.compile(r"^\s{0,3}```\s*([^\s`]*)[^\n`]*\s*$")
    # Matches a closing ``` with optional leading/trailing whitespace.
    _FENCE_CLOSE_RE = re.compile(r"^\s{0,3}```\s*$")

    result: list[str] = []
    inside_non_mermaid = False

    for line in draft.splitlines(keepends=True):
        stripped = line.rstrip("\n").rstrip("\r")
        if not inside_non_mermaid:
            if _FENCE_CLOSE_RE.match(stripped):
                result.append(line)
                continue
            m = _FENCE_OPEN_RE.match(stripped)
            lang = (m.group(1) or "").lower() if m else ""
            if m and lang != "mermaid":
                # Opening fence of a non-mermaid block — skip this line
                inside_non_mermaid = True
                continue
            result.append(line)
        else:
            if _FENCE_CLOSE_RE.match(stripped):
                # Closing fence — skip this line, resume normal mode
                inside_non_mermaid = False
                continue
            result.append(line)

    return "".join(result)


def _strip_preamble_and_ensure_header(content: str, title: str) -> str:
    """Strip reasoning preamble and ensure the page starts with # title.

    Models sometimes output chain-of-thought before the actual Markdown.
    This function:
    1. Strips any text that appears before the first Markdown heading.
    2. Ensures the first heading is the level-1 page title; prepends it if absent.
    """
    m = _HEADING_RE.search(content)
    if m:
        content = content[m.start() :]

    expected_h1 = f"# {title}"
    first_line, sep, rest = content.partition("\n")
    if first_line.startswith("# "):
        if first_line != expected_h1:
            content = expected_h1 + (sep + rest if sep else "")
    else:
        content = f"{expected_h1}\n\n{content}"

    return content


def compute_generation_order(plan: WikiPlan) -> list[list[WikiPageSpec]]:
    """Return pages grouped by depth level, deepest first.

    Pages at the same depth have no parent-child relationship and can be
    generated in parallel. Returns [[deepest], ..., [roots]].
    """
    title_to_page = {p.title: p for p in plan.pages}
    _COMPUTING = object()  # sentinel to detect cycles
    depths: dict[str, int | object] = {}

    def _get_depth(title: str) -> int:
        if title in depths:
            val = depths[title]
            return 0 if val is _COMPUTING else val  # treat cycle as root
        page = title_to_page.get(title)
        if page is None or page.parent is None or page.parent not in title_to_page:
            depths[title] = 0
            return 0
        depths[title] = _COMPUTING  # mark in-progress
        d = _get_depth(page.parent) + 1
        depths[title] = d
        return d

    for p in plan.pages:
        _get_depth(p.title)

    max_depth = max((v for v in depths.values() if isinstance(v, int)), default=0)
    levels: list[list[WikiPageSpec]] = []
    for d in range(max_depth, -1, -1):
        level = [p for p in plan.pages if depths.get(p.title, 0) == d]
        if level:
            levels.append(level)

    return levels


@dataclass
class PageResult:
    """The output of a single wiki page generation call.

    Wraps the LLM-generated Markdown content together with the routing
    identifiers needed to store the page in the database and serve it via the
    REST API.

    Attributes:
        slug: URL-safe identifier derived from the page title (e.g.
            ``"api-gateway"``).  Matches :attr:`WikiPageSpec.slug`.
        title: Human-readable page title (e.g. ``"API Gateway"``).
        content: Full page content as a Markdown string, including optional
            Mermaid diagram code blocks and source-citation annotations.
    """

    slug: str
    title: str
    content: str  # Markdown


PageResultCallback = Callable[[PageResult, WikiPageSpec], Awaitable[None]]


async def generate_page(
    spec: WikiPageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    fast_llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    top_k: int = 30,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
    child_contents: list[PageResult] | None = None,
    repo_notes: list[dict] | None = None,
    on_progress: PageProgressCallback | None = None,
) -> PageResult:
    """Generate a wiki page using the 4-pass pipeline.

    Pass 1 (outline): fast_llm produces a structured outline.
    Pass 2 (draft): llm generates full Markdown from the outline.
    Pass 3 (fact-check): fast_llm verifies key claims against source.
    Pass 4 (revision): llm fixes issues if fact-check fails (max 1 attempt).
    """
    from worker.pipeline.diagram_post_processor import ensure_diagram_headers
    from worker.pipeline.fact_check import (
        run_fact_check,
        run_targeted_revision,
        strip_failed_claim,
        strip_failed_diagram,
    )
    from worker.pipeline.page_draft import build_draft_prompt, generate_draft
    from worker.pipeline.page_outline import generate_page_outline
    from worker.utils.mermaid import sanitize_mermaid_blocks

    # ── RAG retrieval ──
    queries = [f"{spec.title} {' '.join((spec.files or [])[:5])}"]
    if spec.purpose:
        queries.append(spec.purpose)
    if entity_details:
        entity_names = [e.get("name", "") for e in entity_details[:5] if e.get("name")]
        if entity_names:
            queries.append(" ".join(entity_names))

    query_vecs = []
    for q in queries:
        vec = await async_retry(
            embedding.embed,
            q,
            initial_delay=10.0,
            max_retries=5,
            max_delay=120.0,
            transient_exceptions=TRANSIENT_EXCEPTIONS,
            on_retry=on_retry,
        )
        query_vecs.append(vec)

    if len(query_vecs) > 1:
        context_chunks = store.multi_search(query_vecs, k=top_k, doc_k=1)
    else:
        context_chunks = store.search(query_vecs[0], k=top_k, doc_k=1)

    # ── Build reusable context strings ──
    entity_cap = max(25, 8 * len(spec.files or []))
    entity_summaries = _format_entity_details(entity_details or [], entity_cap)
    dep_info_str = None
    if dep_info:
        dep_lines = []
        for key in ("depends_on", "depended_by", "external_deps"):
            vals = dep_info.get(key, [])
            if vals:
                dep_lines.append(f"- {key}: {', '.join(str(v) for v in vals[:10])}")
        dep_info_str = "\n".join(dep_lines) if dep_lines else None

    child_titles = [c.title for c in child_contents] if child_contents else None

    # ── Pass 1: Outline (fast model) ──
    await _report_page_progress(on_progress, spec.title, "Outline")
    outline = await generate_page_outline(
        spec=spec,
        entity_summaries=entity_summaries,
        dep_info=dep_info_str,
        fast_llm=fast_llm,
        on_retry=on_retry,
        child_titles=child_titles,
        wiki_language=wiki_language,
    )

    # ── Pass 2: Draft (main model) ──
    await _report_page_progress(on_progress, spec.title, "Draft")
    draft = await generate_draft(
        spec=spec,
        outline=outline,
        context_chunks=context_chunks,
        repo_name=repo_name,
        llm=llm,
        dep_info=dep_info,
        entity_details=entity_details,
        child_contents=child_contents,
        on_retry=on_retry,
        wiki_language=wiki_language,
        repo_notes=repo_notes,
    )

    # ── Pass 3: Fact-check (fast model) ──
    await _report_page_progress(on_progress, spec.title, "Fact-check")
    targeted_chunks = _format_context_chunks(context_chunks)
    fc_result = await run_fact_check(
        draft=draft,
        outline=outline,
        entity_summaries=entity_summaries,
        dep_info=dep_info_str,
        targeted_chunks=targeted_chunks,
        fast_llm=fast_llm,
        on_retry=on_retry,
        wiki_language=wiki_language,
    )

    # ── Pass 4: Targeted revision (main model, conditional) ──
    if fc_result.verdict == "fail" and fc_result.issues:
        await _report_page_progress(on_progress, spec.title, "Revision")
        context_segments = build_draft_prompt(
            spec=spec,
            outline=outline,
            context_chunks=context_chunks,
            repo_name=repo_name,
            dep_info=dep_info,
            entity_details=entity_details,
            child_contents=child_contents,
            repo_notes=repo_notes,
        )
        cache_segs = [s for s in context_segments if s.cacheable]

        try:
            draft = await run_targeted_revision(
                draft=draft,
                issues=fc_result.issues,
                context_segments=cache_segs,
                llm=llm,
                on_retry=on_retry,
                wiki_language=wiki_language,
            )
        except Exception as exc:
            from worker.pipeline.pipeline_logging import log_final_failure

            log_final_failure(
                logger,
                stage="page_generator.revision",
                exc=exc,
                context={
                    "page_title": spec.title,
                    "issue_count": len(fc_result.issues),
                },
            )
            # Revision failed — deterministic fallback: strip all flagged issues
            for issue in fc_result.issues:
                if issue.kind == "claim" and issue.claim:
                    draft = strip_failed_claim(
                        draft, issue.claim, issue.reason, issue.section
                    )
                elif issue.kind == "diagram" and issue.diagram_index is not None:
                    draft = strip_failed_diagram(
                        draft, issue.section, issue.diagram_index, issue.reason
                    )

    # ── Post-processing ──
    draft = _strip_preamble_and_ensure_header(draft, spec.title)
    draft = ensure_diagram_headers(draft, default_source_files=spec.files)
    draft = sanitize_mermaid_blocks(draft)
    draft = _strip_code_blocks(draft)
    draft = _append_source_files_table(draft, spec.files or [])

    return PageResult(slug=spec.slug, title=spec.title, content=draft)


async def _report_page_progress(
    on_progress: PageProgressCallback | None, title: str, stage: str | None
) -> None:
    if on_progress is not None:
        await on_progress(title, stage)


async def generate_page_batch(
    specs_with_children: list[tuple[WikiPageSpec, list[PageResult] | None]],
    store: FAISSStore,
    llm: LLMProvider,
    fast_llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    file_analysis: FileAnalysis,
    dep_graph: DependencyGraph,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
    repo_notes: list[dict] | None = None,
    on_progress: PageProgressCallback | None = None,
    on_result: PageResultCallback | None = None,
) -> list[PageResult]:
    """Generate all pages in a batch using the multi-pass pipeline."""
    import asyncio

    from worker.pipeline.dependency_graph import summarize_page_deps

    async def _gen_one(
        spec: WikiPageSpec, children: list[PageResult] | None
    ) -> PageResult:
        entities = []
        for rel_path in spec.files or []:
            file_info = file_analysis.files.get(rel_path)
            if file_info:
                for e in file_info.entities:
                    entities.append({**e, "file": rel_path})

        dep_info = summarize_page_deps(spec.files or [], dep_graph)
        dep_info_or_none = dep_info if any(dep_info.values()) else None
        entities_or_none = entities if entities else None

        return await generate_page(
            spec=spec,
            store=store,
            llm=llm,
            fast_llm=fast_llm,
            embedding=embedding,
            repo_name=repo_name,
            dep_info=dep_info_or_none,
            entity_details=entities_or_none,
            on_retry=on_retry,
            wiki_language=wiki_language,
            child_contents=children,
            repo_notes=repo_notes,
            on_progress=on_progress,
        )

    try:
        _concurrency = int(os.getenv("AUTOWIKI_PAGE_CONCURRENCY", "4"))
        if _concurrency < 1:
            _concurrency = 4
    except ValueError:
        _concurrency = 4
    sem = asyncio.Semaphore(_concurrency)

    async def _bounded(
        spec: WikiPageSpec, children: list[PageResult] | None
    ) -> PageResult:
        async with sem:
            try:
                result = await _gen_one(spec, children)
                if on_result is not None:
                    await on_result(result, spec)
                return result
            finally:
                await _report_page_progress(on_progress, spec.title, None)

    results = await asyncio.gather(
        *[_bounded(spec, children) for spec, children in specs_with_children]
    )
    return list(results)
