from __future__ import annotations

import os

from worker.llm.base import LLMProvider


def _build_provider(cfg, model: str) -> LLMProvider:
    """Build a raw (non-logging) LLMProvider for the given model name."""
    from worker.llm.anthropic_provider import AnthropicProvider
    from worker.llm.gemini_provider import GeminiProvider
    from worker.llm.ollama_provider import OllamaProvider
    from worker.llm.openai_provider import OpenAIProvider

    p = cfg.llm.provider
    if p == "anthropic":
        return AnthropicProvider(
            api_key=cfg.llm.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            model=model,
        )
    elif p == "google":
        return GeminiProvider(
            api_key=cfg.llm.api_key or os.environ.get("GOOGLE_API_KEY", ""),
            model=model,
        )
    elif p in ("openai", "openai-compatible"):
        return OpenAIProvider(
            api_key=cfg.llm.api_key or os.environ.get("OPENAI_API_KEY", ""),
            model=model,
            base_url=cfg.llm.base_url or None,
        )
    elif p == "ollama":
        return OllamaProvider(
            model=model,
            base_url=cfg.llm.base_url or "http://localhost:11434",
        )
    else:
        raise ValueError(f"Unknown LLM provider: {p}")


def make_llm_provider(cfg) -> LLMProvider:
    """Factory: create LLMProvider from config."""
    from worker.llm.base import LoggingLLMProvider

    provider = _build_provider(cfg, cfg.llm.model)
    if cfg.debug or os.environ.get("AUTOWIKI_DEBUG", "").lower() == "true":
        return LoggingLLMProvider(provider)
    return provider


def make_fast_llm_provider(cfg, main_provider: LLMProvider) -> LLMProvider:
    """Create the fast LLM provider for classification/verification tasks.

    If ``cfg.llm.fast_model`` is empty or matches ``cfg.llm.model``,
    returns *main_provider* directly (single-model mode).
    Otherwise creates a new provider instance with the fast model name.
    """
    fast_model = cfg.llm.fast_model
    if not fast_model or fast_model == cfg.llm.model:
        return main_provider

    from worker.llm.base import LoggingLLMProvider

    provider = _build_provider(cfg, fast_model)
    if cfg.debug or os.environ.get("AUTOWIKI_DEBUG", "").lower() == "true":
        return LoggingLLMProvider(provider)
    return provider
