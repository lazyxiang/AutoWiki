from __future__ import annotations

import os

from worker.embedding.base import EmbeddingProvider


def make_embedding_provider(cfg) -> EmbeddingProvider:
    """Factory: create EmbeddingProvider from config.

    Import here so worker/jobs.py patches cleanly.
    """
    from worker.embedding.gemini_embed import GeminiEmbedding
    from worker.embedding.ollama_embed import OllamaEmbedding
    from worker.embedding.openai_embed import OpenAIEmbedding

    p = cfg.embedding.provider
    if p == "openai":
        return OpenAIEmbedding(
            api_key=cfg.embedding.api_key or os.environ.get("OPENAI_API_KEY", ""),
            model=cfg.embedding.model,
        )
    elif p == "google":
        return GeminiEmbedding(
            api_key=cfg.embedding.api_key or os.environ.get("GOOGLE_API_KEY", ""),
            model=cfg.embedding.model,
        )
    elif p == "ollama":
        return OllamaEmbedding(model=cfg.embedding.model)
    else:
        raise ValueError(f"Unknown embedding provider: {p}")
