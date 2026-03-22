from __future__ import annotations
import numpy as np
from worker.embedding.base import EmbeddingProvider

# TODO: migrate to google-genai package (google-generativeai is end-of-life)
# New API: from google import genai; client = genai.Client(api_key=...)
try:
    import google.generativeai as genai
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False


class GeminiEmbedding(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "models/text-embedding-004"):
        if not _GENAI_AVAILABLE:
            raise ImportError("google-generativeai is required: pip install google-generativeai")
        genai.configure(api_key=api_key)
        self._model = model
        self._dim = 768

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, text: str) -> np.ndarray:
        result = await genai.embed_content_async(
            model=self._model,
            content=text,
            task_type="retrieval_document",
        )
        return np.array(result["embedding"], dtype=np.float32)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        result = await genai.embed_content_async(
            model=self._model,
            content=texts,
            task_type="retrieval_document",
        )
        return [np.array(e, dtype=np.float32) for e in result["embedding"]]
