"""Chunking primitives for wiki page retrieval.

Provides entity-aware and line-based file chunking, and a ``build_chunks``
helper that wraps them for bulk indexing over a file list.

Moved here from ``worker.pipeline.retrieval.rag_indexer`` (deleted in B2.5)
so that the chunking logic remains available without pulling in FAISS/embedding
dependencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from worker.pipeline.retrieval.chunk import Chunk


def is_code_file(path: Path) -> bool:
    """Return ``True`` if the file is treated as source code (not documentation).

    Documentation formats (Markdown, reStructuredText, plain text, etc.) are
    handled separately from source code by some embedding providers.  The
    predicate is also used internally to decide the default chunking strategy.

    Args:
        path: Path to the file being classified.

    Returns:
        bool: ``False`` if the file extension indicates a documentation format;
        ``True`` for everything else (source code, config, etc.).

    Example:
        >>> is_code_file(Path("README.md"))
        False
        >>> is_code_file(Path("worker/pipeline/ingestion.py"))
        True
    """
    doc_exts = {".md", ".txt", ".rst", ".adoc", ".pdf", ".docx"}
    return path.suffix.lower() not in doc_exts


def chunk_file_with_lines(
    path: Path,
    chunk_size: int = 1000,
    overlap: int = 100,
) -> list[dict[str, Any]]:
    """Split a source file into overlapping text chunks with line-number metadata.

    Uses :class:`langchain_text_splitters.RecursiveCharacterTextSplitter` to
    split the file content and then maps each chunk back to its originating
    line range by searching forward through the raw text.

    Args:
        path: Absolute path to the file to chunk.
        chunk_size: Maximum number of characters per chunk.  Defaults to
            ``1000``.
        overlap: Number of characters of overlap between adjacent chunks.
            Defaults to ``100``.

    Returns:
        list[dict[str, Any]]: A list of chunk dicts, each containing:

        * ``"text"`` (str): The raw chunk text.
        * ``"start_line"`` (int): 1-based line number of the first line of the
          chunk within the original file.
        * ``"end_line"`` (int): 1-based line number of the last line of the
          chunk within the original file.

        Returns an empty list if the file cannot be read or is blank.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=overlap
    )
    chunks = splitter.split_text(text)
    if not chunks:
        chunks = [text]

    results = []
    search_start = 0
    for chunk in chunks:
        idx = text.find(chunk, search_start)
        if idx == -1:
            idx = text.find(chunk)  # fallback: search from beginning
        if idx == -1:
            start_line = text[:search_start].count("\n") + 1
            end_line = start_line + chunk.count("\n")
        else:
            start_line = text[:idx].count("\n") + 1
            end_line = start_line + chunk.count("\n")
            search_start = idx + len(chunk) // 2  # allow overlap

        results.append(
            {
                "text": chunk,
                "start_line": start_line,
                "end_line": end_line,
            }
        )

    return results


def chunk_file_with_entities(
    path: Path,
    entities: list[dict[str, Any]],
    chunk_size: int = 1500,
    overlap: int = 100,
) -> list[dict[str, Any]]:
    """Entity-aware chunking: keep whole functions/classes together when possible.

    For each AST entity (function, class, etc.) extracted by the AST analysis
    stage, attempts to include the entire entity in a single chunk.  If the
    entity text exceeds *chunk_size*, it is split with
    :class:`~langchain_text_splitters.RecursiveCharacterTextSplitter` but each
    sub-chunk is still tagged with the entity name and type.

    Lines that are not covered by any entity (module-level imports, constants,
    top-level statements) are collected into contiguous segments and chunked
    separately without entity metadata.

    Falls back to :func:`chunk_file_with_lines` when *entities* is empty.

    Args:
        path: Absolute path to the file to chunk.
        entities: List of entity dicts as produced by the AST analysis stage.
            Each dict must contain at least ``"start_line"`` and
            ``"end_line"`` (1-based), and optionally ``"name"`` and
            ``"type"``.
        chunk_size: Maximum number of characters per chunk.  Defaults to
            ``1500``.
        overlap: Number of characters of overlap used when splitting oversized
            entities.  Defaults to ``100``.

    Returns:
        list[dict[str, Any]]: Chunk dicts sorted by ``"start_line"``, each
        containing:

        * ``"text"`` (str): Chunk text.
        * ``"start_line"`` (int): 1-based start line.
        * ``"end_line"`` (int): 1-based end line.
        * ``"entity"`` (str | None): Entity name, or ``None`` for uncovered
          segments.
        * ``"entity_type"`` (str | None): Entity type (e.g. ``"function"``,
          ``"class"``), or ``None`` for uncovered segments.

        Returns an empty list if the file cannot be read or is blank.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.strip():
        return []

    lines = text.split("\n")
    if not entities:
        return chunk_file_with_lines(path, chunk_size, overlap)

    # Sort entities by start_line
    sorted_ents = sorted(entities, key=lambda e: e.get("start_line", 0))
    results: list[dict[str, Any]] = []
    covered_lines: set[int] = set()

    for ent in sorted_ents:
        start = ent.get("start_line", 1) - 1  # 0-indexed
        end = ent.get("end_line", start + 1)  # exclusive in our usage
        start = max(0, start)
        end = min(len(lines), end)

        entity_text = "\n".join(lines[start:end])

        if len(entity_text) <= chunk_size:
            results.append(
                {
                    "text": entity_text,
                    "start_line": start + 1,
                    "end_line": end,
                    "entity": ent.get("name"),
                    "entity_type": ent.get("type"),
                }
            )
            covered_lines.update(range(start, end))
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=overlap,
            )
            sub_chunks = splitter.split_text(entity_text)
            sub_offset = start
            for sc in sub_chunks:
                lines_in_chunk = sc.count("\n") + 1
                sc_start = sub_offset + 1
                sc_end = sub_offset + lines_in_chunk
                results.append(
                    {
                        "text": sc,
                        "start_line": sc_start,
                        "end_line": sc_end,
                        "entity": ent.get("name"),
                        "entity_type": ent.get("type"),
                    }
                )
                sub_offset = sc_end
            covered_lines.update(range(start, end))

    # Chunk any remaining uncovered lines (imports, constants, etc.)
    uncovered_segments: list[tuple[int, int]] = []
    seg_start = None
    for i in range(len(lines)):
        if i not in covered_lines:
            if seg_start is None:
                seg_start = i
        else:
            if seg_start is not None:
                uncovered_segments.append((seg_start, i))
                seg_start = None
    if seg_start is not None:
        uncovered_segments.append((seg_start, len(lines)))

    for seg_s, seg_e in uncovered_segments:
        seg_text = "\n".join(lines[seg_s:seg_e])
        if not seg_text.strip():
            continue
        if len(seg_text) <= chunk_size:
            results.append(
                {
                    "text": seg_text,
                    "start_line": seg_s + 1,
                    "end_line": seg_e,
                    "entity": None,
                    "entity_type": None,
                }
            )
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=overlap,
            )
            for sc in splitter.split_text(seg_text):
                results.append(
                    {
                        "text": sc,
                        "start_line": seg_s + 1,
                        "end_line": seg_e,
                        "entity": None,
                        "entity_type": None,
                    }
                )

    results.sort(key=lambda r: r.get("start_line", 0))
    return results


def chunks_from_dicts(dicts: list[dict[str, Any]], *, rel_path: str) -> list[Chunk]:
    """Adapt a list of chunk dicts to :class:`~worker.pipeline.retrieval.chunk.Chunk`.

    Each dict is expected to have at least ``"text"``, ``"start_line"``, and
    ``"end_line"`` keys (as produced by :func:`chunk_file_with_lines` and
    :func:`chunk_file_with_entities`).  Dicts that lack a non-empty ``"text"``
    are silently dropped.

    Args:
        dicts: Chunk dicts as returned by the chunking primitives.
        rel_path: Relative path to use as ``Chunk.file``.

    Returns:
        list[Chunk]: Corresponding ``Chunk`` dataclass instances.
    """
    result: list[Chunk] = []
    for d in dicts:
        text = d.get("text", "")
        if not text:
            continue
        result.append(
            Chunk(
                file=rel_path,
                text=text,
                line_start=d.get("start_line", 0),
                line_end=d.get("end_line", 0),
            )
        )
    return result


def build_chunks(
    files: list[Path],
    root: Path,
    file_entities: dict[str, list[dict[str, Any]]] | None = None,
) -> list[Chunk]:
    """Chunk all files into :class:`~worker.pipeline.retrieval.chunk.Chunk` instances.

    Drop-in replacement for the chunking half of the deleted ``build_rag_index``.
    Domain-agnostic — no LLM, no embedding, no FAISS.

    For each file in *files*:

    1. Computes the relative path (used as ``Chunk.file``).
    2. Selects a chunking strategy:
       - If *file_entities* contains entries for this file, uses
         :func:`chunk_file_with_entities` for richer, entity-tagged output.
       - Otherwise falls back to :func:`chunk_file_with_lines`.
    3. Adapts the resulting dicts to :class:`~worker.pipeline.retrieval.chunk.Chunk`
       via :func:`chunks_from_dicts`.

    Files that cannot be read or produce no chunks are silently skipped.

    Args:
        files: List of absolute :class:`~pathlib.Path` objects pointing to the
            files to chunk.
        root: Repository root directory; used to compute relative paths.
        file_entities: Optional mapping of *relative file path* →
            *list of entity dicts* from the AST analysis stage.  When
            provided, entity-aware chunking is used for matching files.

    Returns:
        list[Chunk]: All chunks across all files, in file order.
    """
    all_chunks: list[Chunk] = []
    for file_path in files:
        try:
            rel = str(file_path.relative_to(root))
        except ValueError:
            rel = str(file_path)

        entities = file_entities.get(rel, []) if file_entities else []
        if entities:
            raw = chunk_file_with_entities(file_path, entities)
        else:
            raw = chunk_file_with_lines(file_path)

        all_chunks.extend(chunks_from_dicts(raw, rel_path=rel))
    return all_chunks
