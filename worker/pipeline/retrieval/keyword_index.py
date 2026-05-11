"""Deterministic BM25 keyword index for wiki page retrieval.

Replaces the previous FAISS-backed vector retrieval for the wiki path.
Pure-Python via ``rank_bm25``; consumes the shared tokenizer from
``worker.utils.tokenize``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from worker.pipeline.retrieval.chunk import Chunk
from worker.utils.tokenize import tokenize_text


@dataclass(frozen=True)
class KeywordIndex:
    chunks: list[Chunk]
    bm25: BM25Okapi | None
    file_to_chunks: dict[str, list[int]]
    token_idf: dict[str, float] = field(default_factory=dict)
    """IDF scores keyed by token. Reserved for Phase A8 ``_score_file_for_page``
    integration (spec §5.3 / §5.2 A8); not consumed internally."""

    @classmethod
    def build(
        cls,
        chunks: list[Chunk],
        *,
        repo_index: dict,  # reserved for Phase A11 scoring integration
    ) -> KeywordIndex:
        if not chunks:
            return cls(chunks=[], bm25=None, file_to_chunks={}, token_idf={})

        tokenized = [list(tokenize_text(c.text)) for c in chunks]
        if not any(tokenized):
            return cls(chunks=chunks, bm25=None, file_to_chunks={}, token_idf={})

        bm25 = BM25Okapi(tokenized)
        file_to_chunks: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(chunks):
            file_to_chunks[c.file].append(i)
        token_idf = dict(bm25.idf) if hasattr(bm25, "idf") else {}
        return cls(
            chunks=chunks,
            bm25=bm25,
            file_to_chunks=dict(file_to_chunks),
            token_idf=token_idf,
        )

    def search(
        self,
        queries: list[str],
        *,
        k: int,
        files: list[str] | None = None,
        per_file_quota: int = 2,
    ) -> list[Chunk]:
        if self.bm25 is None:
            return []

        # Normalize files filter to frozenset for O(1) membership checks.
        file_set = frozenset(files) if files else None

        # union top-k across queries with score-summing for shared chunks
        scores: dict[int, float] = defaultdict(float)
        for q in queries:
            tokens = list(tokenize_text(q))
            for i, s in enumerate(self.bm25.get_scores(tokens)):
                if file_set and self.chunks[i].file not in file_set:
                    continue
                scores[i] += s

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

        if files and per_file_quota > 0:
            quotas = {f: per_file_quota for f in files}
            picked: list[int] = []
            for idx, _ in ranked:
                f = self.chunks[idx].file
                if quotas.get(f, 0) > 0:
                    picked.append(idx)
                    quotas[f] -= 1
                if len(picked) >= k:
                    break
            return [self.chunks[i] for i in picked]

        return [self.chunks[i] for i, _ in ranked[:k]]
