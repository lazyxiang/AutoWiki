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


def chunk_file_with_lines(
    path: Path, chunk_size: int = 1000, overlap: int = 100,
) -> list[dict[str, Any]]:
    """Split a file into chunks, tracking line number ranges for each chunk."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
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

        results.append({
            "text": chunk,
            "start_line": start_line,
            "end_line": end_line,
        })

    return results


def chunk_file_with_entities(
    path: Path,
    entities: list[dict[str, Any]],
    chunk_size: int = 1500,
    overlap: int = 100,
) -> list[dict[str, Any]]:
    """Entity-aware chunking: keep whole functions/classes together when possible.

    Falls back to regular chunking for files without entities or for very large entities.
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
        end = ent.get("end_line", start + 1)    # exclusive in our usage
        start = max(0, start)
        end = min(len(lines), end)

        entity_text = "\n".join(lines[start:end])

        if len(entity_text) <= chunk_size:
            # Entity fits in one chunk — keep it whole
            results.append({
                "text": entity_text,
                "start_line": start + 1,
                "end_line": end,
                "entity": ent.get("name"),
                "entity_type": ent.get("type"),
            })
            covered_lines.update(range(start, end))
        else:
            # Entity too large — split it but tag the chunks
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size, chunk_overlap=overlap,
            )
            sub_chunks = splitter.split_text(entity_text)
            sub_offset = start
            for sc in sub_chunks:
                sc_start = sub_offset + 1
                sc_end = sc_start + sc.count("\n")
                results.append({
                    "text": sc,
                    "start_line": sc_start,
                    "end_line": sc_end,
                    "entity": ent.get("name"),
                    "entity_type": ent.get("type"),
                })
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
            results.append({
                "text": seg_text,
                "start_line": seg_s + 1,
                "end_line": seg_e,
                "entity": None,
                "entity_type": None,
            })
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size, chunk_overlap=overlap,
            )
            for sc in splitter.split_text(seg_text):
                results.append({
                    "text": sc,
                    "start_line": seg_s + 1,
                    "end_line": seg_e,
                    "entity": None,
                    "entity_type": None,
                })

    # Sort by line number for consistent ordering
    results.sort(key=lambda r: r.get("start_line", 0))
    return results


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

    def multi_search(self, queries: list[np.ndarray], k: int = 5) -> list[dict[str, Any]]:
        """Search with multiple queries and merge deduplicated results."""
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
                dedup_key = (meta.get("file", ""), meta.get("start_line", 0), meta.get("chunk_idx", i))
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    results.append(meta)

        return results

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


def is_code_file(path: Path) -> bool:
    """Determine if a file should be treated as source code vs documentation."""
    # Documentation usually has these extensions
    doc_exts = {'.md', '.txt', '.rst', '.adoc', '.pdf', '.docx'}
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
) -> None:
    """Chunk all files, embed, and add to FAISS store.

    Args:
        files: Source files to index.
        root: Repository root for relative paths.
        store: FAISS store to populate.
        embedding_provider: Embedding provider for vectorization.
        file_entities: Optional dict of relative_path -> entity list from AST analysis.
            When provided, uses entity-aware chunking for richer metadata.
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
        vectors = await embedding_provider.embed_batch(texts, is_code=is_code)

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
