from __future__ import annotations

import numpy as np
from openai import AsyncOpenAI

from worker.embedding.base import (
    EmbeddingProvider,
    iter_embedding_batches,
    retry_embedding_call,
)
from worker.utils.retry import OnRetryCallback


class OpenAIEmbedding(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._dim = 1536 if "small" in model else 3072

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str, is_code: bool = False) -> np.ndarray:
        response = await retry_embedding_call(
            self._client.embeddings.create, input=[text], model=self._model
        )
        return np.array(response.data[0].embedding, dtype=np.float32)

    async def embed_batch(
        self,
        texts: list[str],
        is_code: bool = False,
        on_retry: OnRetryCallback | None = None,
    ) -> list[np.ndarray]:
        if not texts:
            return []
        results = []
        for batch in iter_embedding_batches(texts):
            response = await retry_embedding_call(
                self._client.embeddings.create,
                input=batch,
                model=self._model,
                on_retry=on_retry,
            )
            results.extend(
                np.array(d.embedding, dtype=np.float32) for d in response.data
            )
        return results
