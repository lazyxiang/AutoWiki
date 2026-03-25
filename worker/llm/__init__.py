from __future__ import annotations

import os

from worker.llm.base import LLMProvider


def make_llm_provider(cfg) -> LLMProvider:
    """Factory: create LLMProvider from config.

    Import here so worker/jobs.py patches cleanly.
    """
    from worker.llm.anthropic_provider import AnthropicProvider
    from worker.llm.gemini_provider import GeminiProvider
    from worker.llm.ollama_provider import OllamaProvider
    from worker.llm.openai_provider import OpenAIProvider

    p = cfg.llm.provider
    if p == "anthropic":
        return AnthropicProvider(
            api_key=cfg.llm.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            model=cfg.llm.model,
        )
    elif p == "google":
        return GeminiProvider(
            api_key=cfg.llm.api_key or os.environ.get("GOOGLE_API_KEY", ""),
            model=cfg.llm.model,
        )
    elif p in ("openai", "openai-compatible"):
        return OpenAIProvider(
            api_key=cfg.llm.api_key or os.environ.get("OPENAI_API_KEY", ""),
            model=cfg.llm.model,
            base_url=cfg.llm.base_url or None,
        )
    elif p == "ollama":
        return OllamaProvider(
            model=cfg.llm.model,
            base_url=cfg.llm.base_url or "http://localhost:11434",
        )
    else:
        raise ValueError(f"Unknown LLM provider: {p}")
