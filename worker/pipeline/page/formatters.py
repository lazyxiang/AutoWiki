# worker/pipeline/page_formatters.py
"""Shared prompt-formatting helpers used by page_draft and page_generator."""

from __future__ import annotations

from typing import Any


def _rank_weighted_quotas(files: list[str], k: int, floor: int = 2) -> list[int]:
    """Compute a rank-weighted per-file quota that sums to *k*.

    For each file at rank ``i`` (0-indexed), the weight is
    ``w_i = 1 / (i + 1)`` for ``i < 6`` and ``0`` otherwise.  The per-file
    quota is ``max(floor, round(k * w_i / Σw))``.  Rounding is corrected so
    that the quotas sum to ``k``: surplus is added to the top-ranked file,
    deficit is removed from the lowest-rank files (down to the floor).

    Used by both :func:`_format_entity_details` (entity quota) and
    :func:`worker.pipeline.page.generator._balance_chunks` (chunk quota) so
    the two budgets share the same graduated allocation curve.

    Args:
        files: Ranked list of file paths (rank 0 = most important).  Must be
            non-empty; callers should special-case the empty list.
        k: Total budget to distribute across *files*.
        floor: Minimum allocation guaranteed per file.

    Returns:
        list[int]: One quota per file in *files*, summing to *k* (assuming
        ``k >= floor * len(files)``; otherwise quotas may exceed *k* to
        respect the floor).
    """
    weights = [1.0 / (i + 1) if i < 6 else 0.0 for i in range(len(files))]
    total_w = sum(weights) or 1.0  # guard against pathological inputs
    quotas = [max(floor, round(k * w / total_w)) if w > 0 else floor for w in weights]

    # Adjust for rounding so sum(quotas) == k.
    diff = k - sum(quotas)
    if diff > 0:
        quotas[0] += diff
    elif diff < 0:
        # Trim from the lowest-ranked files first, never below the floor.
        for i in range(len(quotas) - 1, -1, -1):
            take = min(-diff, quotas[i] - floor)
            if take > 0:
                quotas[i] -= take
                diff += take
            if diff == 0:
                break
    return quotas


def _format_entity_details(
    entities: list[dict[str, Any]],
    max_entities: int = 25,
    *,
    files: list[str] | None = None,
    floor: int = 2,
) -> str:
    """Format a list of AST entity dicts into a Markdown bullet list for the prompt.

    Renders up to *max_entities* entities (to avoid excessive prompt length),
    showing each entity's type, name, signature, docstring excerpt, and
    source location.

    When *files* is provided, the quota is distributed across files using a
    rank-weighted allocation (the same curve used for chunk balancing in
    :func:`worker.pipeline.page.generator._balance_chunks`): the top-ranked
    file receives the lion's share of entity slots; lower-ranked files keep
    a small visibility floor.  When *files* is ``None``, the function falls
    back to the legacy behaviour of taking the first *max_entities* entries
    in order.

    Args:
        entities: List of entity dicts as produced by the AST analysis stage.
            Recognised keys: ``"type"``, ``"name"``, ``"signature"``,
            ``"docstring"``, ``"file"``, ``"start_line"``, ``"end_line"``.
        max_entities: Maximum number of entities to render. Callers with
            multi-file pages typically scale this with the file count, e.g.
            ``max(25, 8 * len(spec.files))``.
        files: Optional ranked list of file paths (rank 0 = most important).
            When provided, applies a rank-weighted per-file quota so the top
            file gets the most entity detail in the prompt.
        floor: Minimum entity count guaranteed per file when *files* is
            provided (subject to actual entity supply per file).

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

    if files:
        # Rank-weighted: bucket entities by file (preserving input order),
        # then take the per-file quota from each bucket.
        quotas = _rank_weighted_quotas(files, max_entities, floor=floor)
        by_file: dict[str, list[dict[str, Any]]] = {f: [] for f in files}
        leftovers: list[dict[str, Any]] = []
        for e in entities:
            f = e.get("file")
            if f in by_file:
                by_file[f].append(e)
            else:
                leftovers.append(e)

        selected: list[dict[str, Any]] = []
        ranked_remaining: list[dict[str, Any]] = []
        for f, q in zip(files, quotas, strict=False):
            bucket = by_file[f]
            selected.extend(bucket[:q])
            ranked_remaining.extend(bucket[q:])
        while len(selected) < max_entities and ranked_remaining:
            selected.append(ranked_remaining.pop(0))
        # Fill any remaining budget from leftovers (entities whose file is
        # not in the ranked list).
        while len(selected) < max_entities and leftovers:
            selected.append(leftovers.pop(0))
        chosen = selected[:max_entities]
    else:
        chosen = entities[:max_entities]  # Legacy: cap to avoid prompt bloat

    lines = []
    for e in chosen:
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
            :meth:`~worker.pipeline.retrieval.rag_indexer.FAISSStore.search` or
            :meth:`~worker.pipeline.retrieval.rag_indexer.FAISSStore.multi_search`.
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
