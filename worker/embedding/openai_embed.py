from __future__ import annotations

import numpy as np
from openai import AsyncOpenAI

from worker.embedding.base import EmbeddingProvider


class OpenAIEmbedding(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._dim = 1536 if "small" in model else 3072

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str, is_code: bool = False) -> np.ndarray:
        response = await self._client.embeddings.create(input=[text], model=self._model)
        return np.array(response.data[0].embedding, dtype=np.float32)

    async def embed_batch(
        self, texts: list[str], is_code: bool = False
    ) -> list[np.ndarray]:
        if not texts:
            return []
        response = await self._client.embeddings.create(input=texts, model=self._model)
        return [np.array(d.embedding, dtype=np.float32) for d in response.data]
