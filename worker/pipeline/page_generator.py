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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from worker.embedding.base import EmbeddingProvider
from worker.llm.base import LLMProvider
from worker.pipeline.rag_indexer import FAISSStore
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

if TYPE_CHECKING:
    from worker.pipeline.ast_analysis import FileAnalysis
    from worker.pipeline.dependency_graph import DependencyGraph


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


def _format_entity_details(entities: list[dict[str, Any]]) -> str:
    """Format a list of AST entity dicts into a Markdown bullet list for the prompt.

    Renders up to 25 entities (to avoid excessive prompt length), showing
    each entity's type, name, signature, docstring excerpt, and source
    location.

    Args:
        entities: List of entity dicts as produced by the AST analysis stage.
            Recognised keys: ``"type"``, ``"name"``, ``"signature"``,
            ``"docstring"``, ``"file"``, ``"start_line"``, ``"end_line"``.

    Returns:
        str: A multi-line Markdown bullet list where each entity occupies one
        or more lines.  Returns ``"No entity details available."`` when
        *entities* is empty.

    Example:
        >>> entities = [{"type": "function", "name": "parse_github_url",
        ...              "signature": "(url: str) -> tuple[str, str]",
        ...              "docstring": "Parse a GitHub URL.",
        ...              "file": "ingestion.py", "start_line": 70,
        ...              "end_line": 79}]
        >>> print(_format_entity_details(entities))
        - **function** `parse_github_url`
          Signature: `(url: str) -> tuple[str, str]`
          Doc: Parse a GitHub URL.
          Location: ingestion.py:70-79
    """
    if not entities:
        return "No entity details available."
    lines = []
    for e in entities[:25]:  # Cap to avoid prompt bloat
        parts = [f"- **{e.get('type', 'unknown')}** `{e.get('name', '?')}`"]
        if e.get("signature"):
            parts.append(f"  Signature: `{e['signature']}`")
        if e.get("docstring"):
            doc = e["docstring"][:150]
            parts.append(f"  Doc: {doc}")
        if e.get("start_line") and e.get("file"):
            parts.append(
                f"  Location: {e['file']}:{e['start_line']}-{e.get('end_line', '?')}"
            )
        lines.append("\n".join(parts))
    return "\n".join(lines)


def _format_context_chunks(context_chunks: list[dict]) -> str:
    """Format a list of RAG-retrieved chunk dicts into fenced code blocks.

    Each chunk is rendered as a header line (file path, line range, and
    optional entity name) followed by a fenced code block containing the
    chunk text.  Chunks are separated by ``---`` dividers so the LLM can
    easily distinguish them.

    Args:
        context_chunks: List of chunk metadata dicts as returned by
            :meth:`~worker.pipeline.rag_indexer.FAISSStore.search` or
            :meth:`~worker.pipeline.rag_indexer.FAISSStore.multi_search`.
            Expected keys: ``"file"`` (str), ``"start_line"`` (int),
            ``"end_line"`` (int), ``"entity"`` (str | None),
            ``"text"`` (str).

    Returns:
        str: A string containing one fenced code block per chunk, each
        preceded by a header, with ``\\n\\n---\\n\\n`` between chunks.
        Returns ``"No source code context available."`` when
        *context_chunks* is empty.

    Example:
        >>> chunks = [{"file": "api/routes.py", "start_line": 10,
        ...            "end_line": 25, "entity": "list_repos",
        ...            "text": "@app.get('/repos')\\nasync def list_repos():"}]
        >>> print(_format_context_chunks(chunks))
        File: api/routes.py (lines 10-25) [list_repos]
        ```
        @app.get('/repos')
        async def list_repos():
        ```
    """
    if not context_chunks:
        return "No source code context available."
    sections = []
    for c in context_chunks:
        file_path = c.get("file", "unknown")
        start = c.get("start_line", 0)
        end = c.get("end_line", 0)
        entity = c.get("entity")

        header = f"File: {file_path}"
        if start and end:
            header += f" (lines {start}-{end})"
        if entity:
            header += f" [{entity}]"

        sections.append(f"{header}\n```\n{c.get('text', '')}\n```")
    return "\n\n---\n\n".join(sections)


async def generate_page(
    spec: WikiPageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    fast_llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    top_k: int = 12,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
    child_contents: list[PageResult] | None = None,
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
            transient_exceptions=TRANSIENT_EXCEPTIONS,
            on_retry=on_retry,
        )
        query_vecs.append(vec)

    if len(query_vecs) > 1:
        context_chunks = store.multi_search(query_vecs, k=top_k, doc_k=1)
    else:
        context_chunks = store.search(query_vecs[0], k=top_k, doc_k=1)

    # ── Build reusable context strings ──
    entity_summaries = _format_entity_details(entity_details or [])
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
    )

    # ── Pass 3: Fact-check (fast model) ──
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
        context_segments = build_draft_prompt(
            spec=spec,
            outline=outline,
            context_chunks=context_chunks,
            repo_name=repo_name,
            dep_info=dep_info,
            entity_details=entity_details,
            child_contents=child_contents,
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
        except Exception:
            # Revision failed — deterministic fallback: strip all flagged issues
            for issue in fc_result.issues:
                if issue.kind == "claim" and issue.claim:
                    draft = strip_failed_claim(draft, issue.claim, issue.reason)
                elif issue.kind == "diagram" and issue.diagram_index is not None:
                    draft = strip_failed_diagram(
                        draft, issue.section, issue.diagram_index, issue.reason
                    )

    # ── Post-processing ──
    draft = ensure_diagram_headers(draft, default_source_files=spec.files)
    draft = sanitize_mermaid_blocks(draft)

    return PageResult(slug=spec.slug, title=spec.title, content=draft)


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
        )

    sem = asyncio.Semaphore(5)

    async def _bounded(
        spec: WikiPageSpec, children: list[PageResult] | None
    ) -> PageResult:
        async with sem:
            return await _gen_one(spec, children)

    results = await asyncio.gather(
        *[_bounded(spec, children) for spec, children in specs_with_children]
    )
    return list(results)
