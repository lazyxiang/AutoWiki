"""Deterministic BM25 keyword index for wiki page retrieval.

Replaces ``FAISSStore.multi_search`` for the wiki path. Pure-Python via
``rank_bm25``; consumes the shared tokenizer from ``worker.utils.tokenize``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from worker.pipeline.retrieval.chunk import Chunk
from worker.utils.tokenize import tokenize_text


@dataclass
class KeywordIndex:
    chunks: list[Chunk]
    bm25: BM25Okapi
    file_to_chunks: dict[str, list[int]]
    token_idf: dict[str, float] = field(default_factory=dict)

    @classmethod
    def build(cls, chunks: list[Chunk], *, repo_index: dict) -> KeywordIndex:
        tokenized = [list(tokenize_text(c.text)) for c in chunks]
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
        # union top-k across queries with score-summing for shared chunks
        scores: dict[int, float] = defaultdict(float)
        for q in queries:
            tokens = list(tokenize_text(q))
            for i, s in enumerate(self.bm25.get_scores(tokens)):
                if files and self.chunks[i].file not in files:
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
