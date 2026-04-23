from __future__ import annotations

import asyncio

import numpy as np

from worker.embedding.base import (
    EmbeddingProvider,
    iter_embedding_batches,
    retry_embedding_call,
)
from worker.utils.retry import OnRetryCallback

try:
    from google import genai
    from google.genai import errors as _genai_errors

    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False


def _unwrap_genai_error(exc: Exception) -> Exception:
    """Convert Gemini quota exhaustion into an exception handled by async_retry."""
    if _GENAI_AVAILABLE and isinstance(exc, _genai_errors.ClientError):
        if getattr(exc, "code", None) == 429:
            return TimeoutError(str(exc))
    return exc


class GeminiEmbedding(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "models/text-embedding-004"):
        if not _GENAI_AVAILABLE:
            raise ImportError("google-genai is required: pip install google-genai")
        # Initialize with v1beta for features like output_dimensionality
        self._client = genai.Client(
            api_key=api_key, http_options={"api_version": "v1beta"}
        )
        self._model = model
        self._dim = 768

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str, is_code: bool = False) -> np.ndarray:
        # Map generic is_code to Gemini-specific task types
        task_type = "CODE_RETRIEVAL_QUERY" if is_code else "RETRIEVAL_DOCUMENT"

        res = await retry_embedding_call(self._embed_content, text, task_type)
        vec = np.array(res.embeddings[0].values, dtype=np.float32)
        return vec

    async def embed_batch(
        self,
        texts: list[str],
        is_code: bool = False,
        on_retry: OnRetryCallback | None = None,
    ) -> list[np.ndarray]:
        if not texts:
            return []

        # Map generic is_code to Gemini-specific task types
        task_type = "CODE_RETRIEVAL_QUERY" if is_code else "RETRIEVAL_DOCUMENT"

        results = []
        # Keep requests below Gemini's hard limit to reduce quota spike risk.
        for batch in iter_embedding_batches(texts):
            res = await retry_embedding_call(
                self._embed_content, batch, task_type, on_retry=on_retry
            )
            batch_vectors = [
                np.array(e.values, dtype=np.float32) for e in res.embeddings
            ]
            results.extend(batch_vectors)

        return results

    async def _embed_content(self, contents: str | list[str], task_type: str):
        try:
            return await asyncio.to_thread(
                self._client.models.embed_content,
                model=self._model,
                contents=contents,
                config={"task_type": task_type, "output_dimensionality": self._dim},
            )
        except Exception as exc:
            raise _unwrap_genai_error(exc) from exc
