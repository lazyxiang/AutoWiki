from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger("worker.llm")


def _parse_json_response(raw: str) -> dict:
    """Strip optional Markdown code fence and parse JSON."""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("\n", 1)
        raw = parts[1] if len(parts) > 1 else parts[0][3:]
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]
    return json.loads(raw)


def _truncate(text: str, max_len: int = 2000) -> str:
    """Truncate long text for logging."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... [truncated {len(text) - max_len} chars]"


class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, system: str = "") -> str:
        """Generate text from a prompt. Returns the full response string."""

    @abstractmethod
    async def generate_structured(
        self, prompt: str, schema: dict[str, Any], system: str = ""
    ) -> dict[str, Any]:
        """Generate and parse a JSON response matching the given schema."""

    @abstractmethod
    async def generate_stream(
        self, prompt: str, system: str = ""
    ) -> AsyncIterator[str]:
        """Async generator that yields text chunks as they arrive."""

    async def generate_batch(
        self,
        prompts: list[str],
        system: str = "",
        max_concurrency: int = 5,
    ) -> list[str]:
        """Generate responses for multiple prompts concurrently.

        Default implementation uses asyncio.gather with a semaphore.
        Providers may override to use native batch APIs.
        """
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")
        sem = asyncio.Semaphore(max_concurrency)

        async def _one(prompt: str) -> str:
            async with sem:
                return await self.generate(prompt, system)

        return list(await asyncio.gather(*[_one(p) for p in prompts]))


class LoggingLLMProvider(LLMProvider):
    """Wrapper that logs all LLM inputs and outputs at DEBUG level."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    async def generate(self, prompt: str, system: str = "") -> str:
        logger.debug(
            "LLM REQUEST (generate): system=%s, prompt=%s",
            _truncate(system),
            _truncate(prompt),
        )
        response = await self._provider.generate(prompt, system)
        logger.debug("LLM RESPONSE (generate): %s", _truncate(response))
        return response

    async def generate_structured(
        self, prompt: str, schema: dict[str, Any], system: str = ""
    ) -> dict[str, Any]:
        logger.debug(
            "LLM REQUEST (structured): system=%s, schema=%s, prompt=%s",
            _truncate(system),
            json.dumps(schema),
            _truncate(prompt),
        )
        response = await self._provider.generate_structured(prompt, schema, system)
        logger.debug("LLM RESPONSE (structured): %s", _truncate(json.dumps(response)))
        return response

    async def generate_stream(
        self, prompt: str, system: str = ""
    ) -> AsyncIterator[str]:
        logger.debug(
            "LLM REQUEST (stream): system=%s, prompt=%s",
            _truncate(system),
            _truncate(prompt),
        )
        logger.debug("LLM RESPONSE (stream): [STARTING STREAM]")
        full_response = []
        async for chunk in self._provider.generate_stream(prompt, system):
            full_response.append(chunk)
            yield chunk
        logger.debug(
            "LLM RESPONSE (stream): [FINISHED] %s", _truncate("".join(full_response))
        )

    async def generate_batch(
        self,
        prompts: list[str],
        system: str = "",
        max_concurrency: int = 5,
    ) -> list[str]:
        logger.debug(
            "LLM REQUEST (batch): %d prompts, system=%s",
            len(prompts),
            _truncate(system),
        )
        results = await self._provider.generate_batch(prompts, system, max_concurrency)
        logger.debug(
            "LLM RESPONSE (batch): %d responses, total %d chars",
            len(results),
            sum(len(r) for r in results),
        )
        return results
