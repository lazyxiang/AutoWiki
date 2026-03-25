from __future__ import annotations

import asyncio

import numpy as np

from worker.embedding.base import EmbeddingProvider

try:
    from google import genai

    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False


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
        self._max_batch_size = 100  # Gemini limit

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str, is_code: bool = False) -> np.ndarray:
        # Map generic is_code to Gemini-specific task types
        task_type = "CODE_RETRIEVAL_QUERY" if is_code else "RETRIEVAL_DOCUMENT"

        res = await asyncio.to_thread(
            self._client.models.embed_content,
            model=self._model,
            contents=text,
            config={"task_type": task_type, "output_dimensionality": self._dim},
        )
        vec = np.array(res.embeddings[0].values, dtype=np.float32)
        return vec

    async def embed_batch(
        self, texts: list[str], is_code: bool = False
    ) -> list[np.ndarray]:
        if not texts:
            return []

        # Map generic is_code to Gemini-specific task types
        task_type = "CODE_RETRIEVAL_QUERY" if is_code else "RETRIEVAL_DOCUMENT"

        results = []
        # Gemini has a 100 item limit per batch request
        for i in range(0, len(texts), self._max_batch_size):
            batch = texts[i : i + self._max_batch_size]
            res = await asyncio.to_thread(
                self._client.models.embed_content,
                model=self._model,
                contents=batch,
                config={"task_type": task_type, "output_dimensionality": self._dim},
            )
            batch_vectors = [
                np.array(e.values, dtype=np.float32) for e in res.embeddings
            ]
            results.extend(batch_vectors)

        return results
