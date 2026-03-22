from __future__ import annotations
import pickle
from pathlib import Path
from typing import Any
import numpy as np
import faiss
from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_file(path: Path, chunk_size: int = 1000, overlap: int = 100) -> list[str]:
    """Split a source file into overlapping text chunks."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.strip():
        return []
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
    chunks = splitter.split_text(text)
    return chunks if chunks else [text]


class FAISSStore:
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

    def search(self, query: np.ndarray, k: int = 5) -> list[dict[str, Any]]:
        self._ensure_index()
        if self._index.ntotal == 0:
            return []
        q = query.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(q)
        k = min(k, self._index.ntotal)
        _, indices = self._index.search(q, k)
        return [self._metas[i] for i in indices[0] if i >= 0]

    def save(self) -> None:
        self._ensure_index()
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self._index_path))
        self._meta_path.write_bytes(pickle.dumps(self._metas))

    def load(self) -> None:
        self._index = faiss.read_index(str(self._index_path))
        self._metas = pickle.loads(self._meta_path.read_bytes())


async def build_rag_index(
    files: list[Path],
    root: Path,
    store: FAISSStore,
    embedding_provider,
) -> None:
    """Chunk all files, embed, and add to FAISS store."""
    for file_path in files:
        chunks = chunk_file(file_path)
        if not chunks:
            continue
        try:
            rel = str(file_path.relative_to(root))
        except ValueError:
            rel = str(file_path)
        vectors = await embedding_provider.embed_batch(chunks)
        metas = [{"text": chunk, "file": rel, "chunk_idx": i}
                 for i, chunk in enumerate(chunks)]
        store.add(vectors, metas)
    store.save()
