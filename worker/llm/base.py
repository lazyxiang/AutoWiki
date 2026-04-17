from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text

logger = logging.getLogger("worker.llm")


def _parse_json_response(raw: str) -> dict:
    """Strip optional Markdown code fence and parse JSON.

    Handles three common Gemini response patterns:
    - Bare JSON object
    - JSON wrapped in ```json ... ``` fences
    - Valid JSON followed by trailing prose (Extra data)
    """
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("\n", 1)
        raw = parts[1] if len(parts) > 1 else parts[0][3:]
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Trailing content after valid JSON — stop at first complete object/array
        obj, _ = json.JSONDecoder().raw_decode(raw)
    if not isinstance(obj, dict):
        raise json.JSONDecodeError(
            f"Expected a JSON object, got {type(obj).__name__}", raw, 0
        )
    return obj


def _truncate(text: str, max_len: int = 2000) -> str:
    """Truncate long text for logging."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... [truncated {len(text) - max_len} chars]"


class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
        """Generate text from a prompt. Returns the full response string."""

    @abstractmethod
    async def generate_structured(
        self, prompt: PromptInput, schema: dict[str, Any], system: PromptInput = ""
    ) -> dict[str, Any]:
        """Generate and parse a JSON response matching the given schema."""

    @abstractmethod
    async def generate_stream(
        self, prompt: PromptInput, system: PromptInput = ""
    ) -> AsyncIterator[str]:
        """Async generator that yields text chunks as they arrive."""

    async def generate_batch(
        self,
        prompts: list[PromptInput],
        system: PromptInput = "",
        max_concurrency: int = 5,
    ) -> list[str]:
        """Generate responses for multiple prompts concurrently.

        Default implementation uses asyncio.gather with a semaphore.
        Providers may override to use native batch APIs.
        """
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")
        sem = asyncio.Semaphore(max_concurrency)

        async def _one(prompt: PromptInput) -> str:
            async with sem:
                return await self.generate(prompt, system)

        return list(await asyncio.gather(*[_one(p) for p in prompts]))


class LoggingLLMProvider(LLMProvider):
    """Wrapper that logs all LLM inputs and outputs at DEBUG level."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
        logger.debug(
            "LLM REQUEST (generate): system=%s, prompt=%s",
            _truncate(segments_to_text(normalize_prompt(system))),
            _truncate(segments_to_text(normalize_prompt(prompt))),
        )
        response = await self._provider.generate(prompt, system=system)
        logger.debug("LLM RESPONSE (generate): %s", _truncate(response))
        return response

    async def generate_structured(
        self, prompt: PromptInput, schema: dict[str, Any], system: PromptInput = ""
    ) -> dict[str, Any]:
        logger.debug(
            "LLM REQUEST (structured): system=%s, schema=%s, prompt=%s",
            _truncate(segments_to_text(normalize_prompt(system))),
            json.dumps(schema),
            _truncate(segments_to_text(normalize_prompt(prompt))),
        )
        response = await self._provider.generate_structured(
            prompt, schema, system=system
        )
        logger.debug("LLM RESPONSE (structured): %s", _truncate(json.dumps(response)))
        return response

    async def generate_stream(
        self, prompt: PromptInput, system: PromptInput = ""
    ) -> AsyncIterator[str]:
        logger.debug(
            "LLM REQUEST (stream): system=%s, prompt=%s",
            _truncate(segments_to_text(normalize_prompt(system))),
            _truncate(segments_to_text(normalize_prompt(prompt))),
        )
        logger.debug("LLM RESPONSE (stream): [STARTING STREAM]")
        full_response = []
        async for chunk in self._provider.generate_stream(prompt, system=system):
            full_response.append(chunk)
            yield chunk
        logger.debug(
            "LLM RESPONSE (stream): [FINISHED] %s", _truncate("".join(full_response))
        )

    async def generate_batch(
        self,
        prompts: list[PromptInput],
        system: PromptInput = "",
        max_concurrency: int = 5,
    ) -> list[str]:
        logger.debug(
            "LLM REQUEST (batch): %d prompts, system=%s",
            len(prompts),
            _truncate(segments_to_text(normalize_prompt(system))),
        )
        results = await self._provider.generate_batch(
            prompts, system=system, max_concurrency=max_concurrency
        )
        logger.debug(
            "LLM RESPONSE (batch): %d responses, total %d chars",
            len(results),
            sum(len(r) for r in results),
        )
        return results
