from __future__ import annotations
import numpy as np
import httpx
from worker.embedding.base import EmbeddingProvider


class OllamaEmbedding(EmbeddingProvider):
    def __init__(self, model: str = "nomic-embed-text", base_url: str = "http://localhost:11434"):
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._dim = 768  # nomic-embed-text default; adjustable

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str) -> np.ndarray:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{self._base_url}/api/embeddings",
                                     json={"model": self._model, "prompt": text})
            resp.raise_for_status()
            return np.array(resp.json()["embedding"], dtype=np.float32)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        return [await self.embed(t) for t in texts]
