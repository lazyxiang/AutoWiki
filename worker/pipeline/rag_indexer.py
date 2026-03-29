"""Stage 4 of the generation pipeline.

Covers entity-aware chunking, FAISS vector indexing, and similarity search.

This module provides three capabilities:

1. **Chunking** — Two strategies for splitting source files into overlapping
   text segments:

   * :func:`chunk_file_with_lines` — generic line-range tracking, suitable for
     documentation and files without AST entities.
   * :func:`chunk_file_with_entities` — entity-aware chunking that keeps whole
     functions and classes together when they fit within *chunk_size*; falls
     back to :func:`chunk_file_with_lines` sub-chunks for oversized entities
     and collects leftover (uncovered) segments separately.

2. **Vector Store** — :class:`FAISSStore` wraps a ``faiss.IndexFlatIP`` (inner
   product on L2-normalised vectors, which is equivalent to cosine similarity)
   and a parallel list of metadata dicts.  Supports single-query and
   multi-query search with deduplication, plus disk persistence.

3. **Index Builder** — :func:`build_rag_index` orchestrates chunking,
   embedding, and adding to the store for an entire file list.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from langchain_text_splitters import RecursiveCharacterTextSplitter

from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry


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

    Example:
        >>> chunks = chunk_file_with_lines(Path("worker/pipeline/ingestion.py"))
        >>> chunks[0].keys()
        dict_keys(['text', 'start_line', 'end_line'])
        >>> chunks[0]["start_line"]
        1
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
            # chunk may have been modified by splitter; estimate position
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

    Example:
        >>> entities = [{"name": "MyClass", "type": "class",
        ...              "start_line": 5, "end_line": 30}]
        >>> chunks = chunk_file_with_entities(Path("mymodule.py"), entities)
        >>> # Entity chunk:
        >>> chunks[0]["entity"]
        'MyClass'
        >>> chunks[0]["entity_type"]
        'class'
        >>> # Uncovered segment chunk (e.g. module imports at lines 1-4):
        >>> chunks[-1]["entity"] is None
        True
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
            # Entity fits in one chunk — keep it whole
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
            # Entity too large — split it but tag the chunks
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

    # Sort by line number for consistent ordering
    results.sort(key=lambda r: r.get("start_line", 0))
    return results


class FAISSStore:
    """Thin wrapper around a ``faiss.IndexFlatIP`` vector store.

    Vectors are L2-normalised before being added or queried, so inner-product
    (IP) search is equivalent to cosine-similarity search.  Metadata dicts are
    stored in a parallel Python list that is serialised alongside the FAISS
    index via :mod:`pickle`.

    Args:
        dimension: Dimensionality of the embedding vectors (e.g. ``1536`` for
            ``text-embedding-3-small``).
        index_path: File path where the FAISS binary index will be written by
            :meth:`save` and read by :meth:`load`.
        meta_path: File path where the pickled metadata list will be written by
            :meth:`save` and read by :meth:`load`.

    Note:
        The index is created lazily on the first call to :meth:`add` or
        :meth:`search`; constructing a :class:`FAISSStore` does not allocate
        any FAISS memory.  Calling :meth:`save` before :meth:`add` will write
        an empty (but valid) index.
    """

    def __init__(self, dimension: int, index_path: Path, meta_path: Path):
        self._dim = dimension
        self._index_path = Path(index_path)
        self._meta_path = Path(meta_path)
        self._index: faiss.IndexFlatIP | None = None
        self._metas: list[dict[str, Any]] = []

    def _ensure_index(self):
        # Lazily initialise the FAISS index on first use.
        if self._index is None:
            self._index = faiss.IndexFlatIP(self._dim)

    def add(self, vectors: list[np.ndarray], metas: list[dict[str, Any]]) -> None:
        """Add a batch of vectors and their associated metadata to the store.

        Each vector is L2-normalised in-place before being added so that
        subsequent inner-product searches are equivalent to cosine similarity.

        Args:
            vectors: List of embedding vectors, one per chunk.  Each element
                must be a 1-D :class:`numpy.ndarray` of shape ``(dimension,)``.
            metas: List of metadata dicts corresponding to *vectors*.  Each
                dict is stored as-is and returned verbatim by :meth:`search`.
                Typical keys: ``"file"``, ``"start_line"``, ``"end_line"``,
                ``"text"``, ``"entity"``, ``"entity_type"``.

        Note:
            *vectors* and *metas* must have the same length.  The
            normalisation is applied to a stacked ``float32`` matrix, so the
            original arrays are not mutated.
        """
        self._ensure_index()
        matrix = np.stack(vectors).astype(np.float32)
        faiss.normalize_L2(matrix)
        self._index.add(matrix)
        self._metas.extend(metas)

    def search(self, query: np.ndarray, k: int = 5) -> list[dict[str, Any]]:
        """Return the *k* most similar chunks to *query*.

        The query vector is L2-normalised before the inner-product search so
        the scores are cosine similarities in the range ``[-1, 1]``.

        Args:
            query: A 1-D :class:`numpy.ndarray` of shape ``(dimension,)``
                representing the embedded query.
            k: Number of nearest neighbours to return.  Clamped to
                ``min(k, index.ntotal)`` automatically.  Defaults to ``5``.

        Returns:
            list[dict[str, Any]]: Up to *k* metadata dicts, ordered by
            descending cosine similarity.  Returns an empty list if the index
            contains no vectors.

        Example:
            >>> vec = embedding_provider.embed("What does the API layer do?")
            >>> results = store.search(np.array(vec), k=5)
            >>> results[0]["file"]
            'api/routes.py'
            >>> results[0]["start_line"]
            12
        """
        self._ensure_index()
        if self._index.ntotal == 0:
            return []
        q = query.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(q)
        k = min(k, self._index.ntotal)
        _, indices = self._index.search(q, k)
        return [self._metas[i] for i in indices[0] if i >= 0]

    def multi_search(
        self, queries: list[np.ndarray], k: int = 5
    ) -> list[dict[str, Any]]:
        """Search with multiple query vectors and return deduplicated results.

        Runs :meth:`search` for each query in *queries* and merges the results
        into a single list, deduplicating by the tuple
        ``(file, start_line, chunk_idx)``.  The output preserves the order in
        which unique chunks are first encountered across queries, so results
        from earlier (usually more important) queries appear first.

        Args:
            queries: List of 1-D query vectors (one per semantic query).  Each
                element is a :class:`numpy.ndarray` of shape ``(dimension,)``.
            k: Number of nearest neighbours to retrieve *per query* before
                deduplication.  Defaults to ``5``.

        Returns:
            list[dict[str, Any]]: Deduplicated metadata dicts from all queries,
            ordered by first appearance.  May contain up to
            ``len(queries) * k`` entries before deduplication.

        Example:
            >>> vecs = [embed("API layer"), embed("FastAPI routes")]
            >>> results = store.multi_search(vecs, k=5)
            >>> len(results) <= 10  # at most k * len(queries) before dedup
            True
        """
        self._ensure_index()
        if self._index.ntotal == 0:
            return []

        seen_keys: set[tuple] = set()
        results: list[dict[str, Any]] = []

        for query in queries:
            q = query.astype(np.float32).reshape(1, -1)
            faiss.normalize_L2(q)
            actual_k = min(k, self._index.ntotal)
            _, indices = self._index.search(q, actual_k)
            for i in indices[0]:
                if i < 0:
                    continue
                meta = self._metas[i]
                # Deduplicate by (file, start_line, chunk_idx) so the same
                # chunk returned by multiple queries is included only once.
                dedup_key = (
                    meta.get("file", ""),
                    meta.get("start_line", 0),
                    meta.get("chunk_idx", i),
                )
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    results.append(meta)

        return results

    def save(self) -> None:
        """Persist the FAISS index and metadata list to disk.

        Writes two files:

        * *index_path* — the FAISS binary index (``faiss.write_index``).
        * *meta_path* — a pickle of the parallel metadata list.

        Parent directories are created automatically.  An empty index is
        written if :meth:`add` has never been called.

        Raises:
            OSError: If the parent directory cannot be created or either file
                cannot be written (e.g. permissions, disk full).
        """
        self._ensure_index()
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self._index_path))
        self._meta_path.write_bytes(pickle.dumps(self._metas))

    def load(self) -> None:
        """Load a previously saved FAISS index and metadata list from disk.

        Reads:

        * *index_path* — the FAISS binary index (``faiss.read_index``).
        * *meta_path* — the pickled metadata list.

        Raises:
            FileNotFoundError: If *index_path* does not exist or is
                unreadable, or if *meta_path* does not exist or is unreadable.

        Note:
            After a successful :meth:`load`, :meth:`search` and
            :meth:`multi_search` will query the loaded index.  Any vectors
            added via :meth:`add` *before* :meth:`load` is called will be
            overwritten.
        """
        try:
            self._index = faiss.read_index(str(self._index_path))
        except Exception as exc:
            raise FileNotFoundError(
                f"FAISS index file not found or unreadable: {self._index_path}"
            ) from exc
        try:
            self._metas = pickle.loads(self._meta_path.read_bytes())
        except (FileNotFoundError, OSError) as exc:
            raise FileNotFoundError(
                f"FAISS metadata file not found or unreadable: {self._meta_path}"
            ) from exc


def is_code_file(path: Path) -> bool:
    """Determine whether a file should be treated as source code.

    Documentation formats (Markdown, reStructuredText, plain text, etc.)
    are embedded differently from source code by some providers.  This
    predicate is used by :func:`build_rag_index` to pass the correct
    ``is_code`` flag to the embedding provider.

    Args:
        path: Path to the file being classified.

    Returns:
        bool: ``False`` if the file extension indicates a documentation
        format; ``True`` for everything else (source code, config, etc.).

    Example:
        >>> is_code_file(Path("README.md"))
        False
        >>> is_code_file(Path("worker/pipeline/ingestion.py"))
        True
        >>> is_code_file(Path("schema.graphql"))
        True
    """
    # Documentation usually has these extensions
    doc_exts = {".md", ".txt", ".rst", ".adoc", ".pdf", ".docx"}
    if path.suffix.lower() in doc_exts:
        return False
    # Most other things we parse are source code (py, js, ts, go, rs, etc.)
    return True


async def build_rag_index(
    files: list[Path],
    root: Path,
    store: FAISSStore,
    embedding_provider,
    file_entities: dict[str, list[dict]] | None = None,
    on_retry: OnRetryCallback | None = None,
) -> None:
    """Chunk all files, embed each chunk, and add the vectors to *store*.

    For each file in *files*:

    1. Determines the relative path (used as the ``"file"`` key in metadata).
    2. Selects a chunking strategy:
       - If *file_entities* contains entries for this file, uses
         :func:`chunk_file_with_entities` for richer, entity-tagged metadata.
       - Otherwise falls back to :func:`chunk_file_with_lines`.
    3. Embeds all chunks in a single batch via *embedding_provider* (with
       automatic retry on transient errors).
    4. Stores vectors and metadata in *store* via :meth:`FAISSStore.add`.

    After all files are processed, calls :meth:`FAISSStore.save` to persist
    the index and metadata to disk.

    Args:
        files: List of absolute :class:`pathlib.Path` objects pointing to
            files to index.
        root: Repository root directory; used to compute relative paths for
            metadata.
        store: A :class:`FAISSStore` instance to populate.  Should be freshly
            constructed (empty) or pre-loaded if doing a partial re-index.
        embedding_provider: An object implementing
            ``async embed_batch(texts: list[str], *, is_code: bool) ->
            list[np.ndarray]``.
        file_entities: Optional mapping of *relative file path* →
            *list of entity dicts* from the AST analysis stage.  When
            provided, entity-aware chunking is used for files that have
            entities, yielding chunks tagged with ``entity`` and
            ``entity_type`` keys.
        on_retry: Optional callback invoked each time ``async_retry`` retries
            an embedding call (useful for progress reporting).

    Returns:
        None: This function has a side-effect only — it populates and saves
        *store*.  The caller should use ``store.search()`` afterwards to
        retrieve indexed chunks.

    Note:
        Files that cannot be read or produce no chunks are silently skipped.
    """
    for file_path in files:
        try:
            rel = str(file_path.relative_to(root))
        except ValueError:
            rel = str(file_path)

        is_code = is_code_file(file_path)
        entities = file_entities.get(rel, []) if file_entities else []

        if entities:
            chunk_data = chunk_file_with_entities(file_path, entities)
        else:
            chunk_data = chunk_file_with_lines(file_path)

        if not chunk_data:
            continue

        texts = [c["text"] for c in chunk_data]
        vectors = await async_retry(
            embedding_provider.embed_batch,
            texts,
            is_code=is_code,
            transient_exceptions=TRANSIENT_EXCEPTIONS,
            on_retry=on_retry,
        )

        metas = []
        for i, cd in enumerate(chunk_data):
            meta: dict[str, Any] = {
                "text": cd["text"],
                "file": rel,
                "chunk_idx": i,
                "start_line": cd.get("start_line", 0),
                "end_line": cd.get("end_line", 0),
            }
            if cd.get("entity"):
                meta["entity"] = cd["entity"]
            if cd.get("entity_type"):
                meta["entity_type"] = cd["entity_type"]
            metas.append(meta)

        store.add(vectors, metas)
    store.save()
