"""FAISSStore kept on disk for ``worker/research/*`` compatibility.

The FAISS-based vector store was removed from the main indexing pipeline in
B2.5 (Stage 4 deletion).  ``worker/research/`` still imports ``FAISSStore``
and will be disabled in B2.6; keeping the class here prevents import errors
during the transition.

New code should NOT use this class.  The canonical retrieval path is
:class:`~worker.pipeline.retrieval.keyword_index.KeywordIndex`.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import faiss
import numpy as np

_DOC_EXTENSIONS = frozenset({".md", ".rst", ".txt", ".adoc"})


def _is_doc_chunk(meta: dict[str, Any]) -> bool:
    """Return True if this chunk comes from a documentation file."""
    file_path = meta.get("file", "")
    return Path(file_path).suffix.lower() in _DOC_EXTENSIONS


def _partition_with_doc_cap(
    chunks: list[dict[str, Any]], *, k: int, doc_k: int
) -> list[dict[str, Any]]:
    """Return up to *k* chunks with at most *doc_k* doc chunks."""
    code_k = k - doc_k
    code_chunks: list[dict[str, Any]] = []
    doc_chunks: list[dict[str, Any]] = []
    extra_code: list[dict[str, Any]] = []
    for meta in chunks:
        if _is_doc_chunk(meta):
            if len(doc_chunks) < doc_k:
                doc_chunks.append(meta)
        else:
            if len(code_chunks) < code_k:
                code_chunks.append(meta)
            else:
                extra_code.append(meta)

    merged = code_chunks + doc_chunks
    deficit = doc_k - len(doc_chunks)
    if deficit > 0 and extra_code:
        merged.extend(extra_code[:deficit])
    return merged[:k]


class FAISSStore:
    """Thin wrapper around a ``faiss.IndexFlatIP`` vector store.

    Kept for ``worker/research/*`` import compatibility after Stage 4 removal.
    Will be retired when Deep Research is disabled in B2.6.
    """

    def __init__(self, dimension: int, index_path: Path, meta_path: Path):
        self._dim = dimension
        self._index_path = Path(index_path)
        self._meta_path = Path(meta_path)
        self._index: faiss.IndexFlatIP | None = None
        self._metas: list[dict[str, Any]] = []

    def _ensure_index(self):
        if self._index is None:
            self._index = faiss.IndexFlatIP(self._dim)

    def add(self, vectors: list[np.ndarray], metas: list[dict[str, Any]]) -> None:
        self._ensure_index()
        matrix = np.stack(vectors).astype(np.float32)
        faiss.normalize_L2(matrix)
        self._index.add(matrix)
        self._metas.extend(metas)

    def search(
        self, query: np.ndarray, k: int = 5, doc_k: int | None = None
    ) -> list[dict[str, Any]]:
        self._ensure_index()
        if self._index.ntotal == 0:
            return []
        q = query.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(q)

        if doc_k is not None:
            doc_k = max(0, min(doc_k, k))

        fetch_k = min(k * 2 if doc_k is not None else k, self._index.ntotal)
        _, indices = self._index.search(q, fetch_k)
        all_results = [self._metas[i] for i in indices[0] if i >= 0]

        if doc_k is None:
            return all_results[:k]

        return _partition_with_doc_cap(all_results, k=k, doc_k=doc_k)

    def multi_search(
        self, queries: list[np.ndarray], k: int = 5, doc_k: int | None = None
    ) -> list[dict[str, Any]]:
        self._ensure_index()
        if self._index.ntotal == 0:
            return []

        if doc_k is not None:
            doc_k = max(0, min(doc_k, k))

        seen_keys: set[tuple] = set()
        results: list[dict[str, Any]] = []

        for query in queries:
            q = query.astype(np.float32).reshape(1, -1)
            faiss.normalize_L2(q)
            actual_k = min(k * 2 if doc_k is not None else k, self._index.ntotal)
            _, indices = self._index.search(q, actual_k)
            for i in indices[0]:
                if i < 0:
                    continue
                meta = self._metas[i]
                dedup_key = (
                    meta.get("file", ""),
                    meta.get("start_line", 0),
                    meta.get("chunk_idx", i),
                )
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    results.append(meta)

        if doc_k is None:
            return results

        return _partition_with_doc_cap(results, k=k, doc_k=doc_k)

    def save(self) -> None:
        self._ensure_index()
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self._index_path))
        self._meta_path.write_bytes(pickle.dumps(self._metas))

    def load(self) -> None:
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
