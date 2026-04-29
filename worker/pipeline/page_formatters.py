# worker/pipeline/page_formatters.py
"""Shared prompt-formatting helpers used by page_draft and page_generator."""

from __future__ import annotations

from typing import Any


def _format_entity_details(
    entities: list[dict[str, Any]], max_entities: int = 25
) -> str:
    """Format a list of AST entity dicts into a Markdown bullet list for the prompt.

    Renders up to *max_entities* entities (to avoid excessive prompt length),
    showing each entity's type, name, signature, docstring excerpt, and
    source location.

    Args:
        entities: List of entity dicts as produced by the AST analysis stage.
            Recognised keys: ``"type"``, ``"name"``, ``"signature"``,
            ``"docstring"``, ``"file"``, ``"start_line"``, ``"end_line"``.
        max_entities: Maximum number of entities to render. Callers with
            multi-file pages typically scale this with the file count, e.g.
            ``max(25, 8 * len(spec.files))``.

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
    for e in entities[:max_entities]:  # Cap to avoid prompt bloat
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
