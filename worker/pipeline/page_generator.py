"""Stage 6 of the generation pipeline.

Covers multi-query RAG retrieval and LLM page generation.

For each :class:`~worker.pipeline.wiki_planner.WikiPageSpec` in the wiki
plan, this module:

1. Constructs two or more semantic search queries (multi-query RAG) based on
   the page title, purpose, and key entity names.
2. Embeds all queries and performs a deduplicated multi-search against the
   :class:`~worker.pipeline.rag_indexer.FAISSStore`.
3. Formats the retrieved source-code chunks, entity details, and dependency
   info into a structured LLM prompt.
4. Calls the LLM (with ``async_retry`` for transient errors) and wraps the
   resulting Markdown in a :class:`PageResult`.

The module uses two distinct prompt templates: a richer *overview* template
for the top-level ``"Overview"`` page (adds Architecture and Getting Started
sections) and a standard *component* template for all other pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worker.embedding.base import EmbeddingProvider
from worker.llm.base import LLMProvider
from worker.pipeline.rag_indexer import FAISSStore
from worker.pipeline.wiki_planner import WikiPageSpec
from worker.utils.mermaid import sanitize_mermaid_blocks
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

_SYSTEM = (
    "You are a senior technical writer creating comprehensive, "
    "production-quality wiki documentation for a software "
    "repository. Your goal is to help developers new to this "
    "codebase understand it quickly and thoroughly.\n\n"
    "Rules:\n"
    "- Every technical claim MUST be grounded in the provided "
    "source code — do not invent APIs, classes, or features "
    "not present in the code context\n"
    "- After each major section or subsection, add a source "
    "annotation in italics citing where the information comes "
    "from: *Source: path/to/file.py:10-45*\n"
    "- Include Mermaid diagrams where they aid understanding "
    "— use ```mermaid code blocks\n"
    "- Choose diagram types that best fit the content:\n"
    "  - flowchart TD for architecture/data flow\n"
    "  - classDiagram for class relationships\n"
    "  - sequenceDiagram for request/response flows\n"
    "  - graph LR for dependency relationships\n"
    "- IMPORTANT Mermaid quoting rules — violating these causes "
    "parse errors:\n"
    '  - Node labels with special chars: A["Server (HTTP)"] '
    "not A[Server (HTTP)]\n"
    '  - Edge labels with special chars: -->|"GET /api/{id}"| '
    "not -->|GET /api/{id}|\n"
    "  - Special characters requiring quotes: ( ) { } | < > /\n"
    "- Write for developers who are new to this codebase but "
    "experienced programmers\n"
    "- Use precise technical language and include concrete "
    "code examples from the source\n"
    "- Organize content from high-level concepts down to "
    "implementation details"
)


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


def _build_page_prompt(
    spec: WikiPageSpec,
    context_chunks: list[dict],
    repo_name: str,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
) -> str:
    """Build the full LLM prompt for generating a single wiki page.

    Assembles a structured prompt from all available context.  The final
    instruction section branches on whether the page is an *overview* page
    (slug equals ``"overview"`` or title contains the word ``"overview"``):

    * **Overview branch** — Requests sections: Overview, Architecture (with
      Mermaid flowchart), Key Components, Getting Started, Technology Stack.
    * **Non-overview branch** — Requests sections: Overview, Architecture
      (optional diagram), Key Components, Dependencies & Interactions, Source
      Files.

    Args:
        spec: The :class:`WikiPageSpec` being generated.
        context_chunks: RAG-retrieved source-code chunk dicts (formatted by
            :func:`_format_context_chunks`).
        repo_name: Human-readable repository name included in the prompt
            heading and instruction text.
        dep_info: Optional dependency info dict with keys ``"depends_on"``
            (list[str]), ``"depended_by"`` (list[str]), and
            ``"external_deps"`` (list[str]).
        entity_details: Optional list of entity dicts formatted by
            :func:`_format_entity_details`.

    Returns:
        str: The complete LLM prompt as a single string, with sections
        separated by blank lines.
    """
    context = _format_context_chunks(context_chunks)

    sections = [
        f"Repository: {repo_name}",
        f"Page: {spec.title}",
    ]

    if spec.purpose:
        sections.append(f"Purpose: {spec.purpose}")

    sections.append(f"Source files: {', '.join(spec.files or [])}")

    # Dependency context
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
            sections.append("Dependencies:\n" + "\n".join(dep_lines))

    # Entity details
    if entity_details:
        sections.append(
            f"Key entities in these modules:\n{_format_entity_details(entity_details)}"
        )

    sections.append(
        f"Relevant source code (with file paths and line numbers):\n{context}"
    )

    is_overview = spec.slug == "overview" or "overview" in spec.title.lower()

    if is_overview:
        sections.append(
            f"Write a comprehensive Overview wiki page for"
            f' the "{repo_name}" project. Structure:\n\n'
            "## Overview\n"
            "What this project does, its primary use cases, "
            "and the problem it solves.\n\n"
            "## Architecture\n"
            "Include a Mermaid diagram (```mermaid flowchart) "
            "showing the major components and how they "
            "connect.\n"
            "Describe the high-level architecture, service "
            "topology, and data flow.\n\n"
            "## Key Components\n"
            "For each major subsystem/module, provide a brief "
            "description of its role.\n"
            "After each component description, cite the "
            "source: *Source: file.py:line-line*\n\n"
            "## Getting Started\n"
            "How a developer would begin working with this "
            "codebase (entry points, key files to read "
            "first).\n\n"
            "## Technology Stack\n"
            "Key frameworks, libraries, and tools used.\n\n"
            "Output Markdown only."
        )
    else:
        sections.append(
            f"Write a comprehensive wiki page for"
            f' "{spec.title}". Structure:\n\n'
            "## Overview\n"
            "Brief description of this component's role, "
            "purpose, and design rationale.\n\n"
            "## Architecture\n"
            "Include a Mermaid diagram if it helps explain "
            "relationships (class diagram, flowchart, or "
            "sequence diagram).\n"
            "Only include a diagram if it adds genuine "
            "value — not every page needs one.\n\n"
            "## Key Components\n"
            "For each major class/function in this module:\n"
            "- What it does and why it exists\n"
            "- Its interface/signature\n"
            "- Key implementation details\n"
            "- Usage example from the codebase if available\n"
            "After each subsection, cite the source: "
            "*Source: file.py:line-line*\n\n"
            "## Dependencies & Interactions\n"
            "How this module connects to other parts of the "
            "codebase.\n"
            "What it depends on and what depends on it.\n\n"
            "## Source Files\n"
            "Table or list of all source files covered by "
            "this page with brief descriptions.\n\n"
            "Output Markdown only."
        )

    return "\n\n".join(sections)


async def generate_page(
    spec: WikiPageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    top_k: int = 12,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
    on_retry: OnRetryCallback | None = None,
) -> PageResult:
    """Generate a single wiki page using multi-query RAG and an LLM.

    **Multi-query RAG strategy**: Instead of a single embedding query, up to
    three queries are constructed and embedded independently:

    1. ``"{title} {first 5 file paths}"`` — anchors the search to the page's
       assigned files.
    2. ``"{purpose}"`` — (added when *spec.purpose* is non-empty) retrieves
       chunks semantically related to the page's stated goal.
    3. ``"{entity_name1} {entity_name2} ..."`` — (added when *entity_details*
       is non-empty) targets chunks that mention specific classes or functions.

    All query vectors are passed to
    :meth:`~worker.pipeline.rag_indexer.FAISSStore.multi_search` (or
    :meth:`~worker.pipeline.rag_indexer.FAISSStore.search` for a single
    query), which deduplicates results so the same chunk is not sent to the
    LLM twice.

    Args:
        spec: The :class:`WikiPageSpec` describing the page to generate.
        store: A loaded :class:`~worker.pipeline.rag_indexer.FAISSStore`
            containing the repository's indexed chunks.
        llm: An :class:`~worker.llm.base.LLMProvider` instance used to
            generate the Markdown content.
        embedding: An :class:`~worker.embedding.base.EmbeddingProvider`
            instance used to embed the search queries.
        repo_name: Human-readable repository name included in the prompt.
        top_k: Number of nearest neighbours to retrieve *per query* before
            deduplication.  Defaults to ``12``.
        dep_info: Optional dependency info dict with keys ``"depends_on"``,
            ``"depended_by"``, and ``"external_deps"`` (all ``list[str]``).
        entity_details: Optional list of entity dicts (classes, functions)
            from the AST analysis stage; used both as additional query text
            and formatted inline in the prompt.
        on_retry: Optional callback invoked on each retry by ``async_retry``
            (useful for progress reporting).

    Returns:
        PageResult: A :class:`PageResult` with ``slug``, ``title``, and
        ``content`` (the LLM-generated Markdown string).

    Example:
        >>> result = await generate_page(
        ...     spec=WikiPageSpec(title="API Gateway", purpose="Handles HTTP.",
        ...                       files=["api/main.py"]),
        ...     store=store,
        ...     llm=llm_provider,
        ...     embedding=embedding_provider,
        ...     repo_name="owner/repo",
        ... )
        >>> result.slug
        'api-gateway'
        >>> result.content[:20]
        '## Overview\\n\\nThe AP'
    """
    # Multi-query RAG: generate multiple semantic queries for better coverage
    queries = [f"{spec.title} {' '.join((spec.files or [])[:5])}"]

    if spec.purpose:
        queries.append(spec.purpose)

    # Add entity names as queries for targeted retrieval
    if entity_details:
        entity_names = [e.get("name", "") for e in entity_details[:5] if e.get("name")]
        if entity_names:
            queries.append(" ".join(entity_names))

    # Embed all queries and do multi-search
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
        context_chunks = store.multi_search(query_vecs, k=top_k)
    else:
        context_chunks = store.search(query_vecs[0], k=top_k)

    prompt = _build_page_prompt(
        spec, context_chunks, repo_name, dep_info, entity_details
    )
    content = await async_retry(
        llm.generate,
        prompt,
        system=_SYSTEM,
        transient_exceptions=TRANSIENT_EXCEPTIONS,
        on_retry=on_retry,
    )

    content = sanitize_mermaid_blocks(content)
    return PageResult(slug=spec.slug, title=spec.title, content=content)
