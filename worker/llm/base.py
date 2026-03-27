from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger("worker.llm")


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


class LoggingLLMProvider(LLMProvider):
    """Wrapper that logs all LLM inputs and outputs at DEBUG level."""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    async def generate(self, prompt: str, system: str = "") -> str:
        logger.debug("LLM REQUEST (generate): system=%s, prompt=%s", system, prompt)
        response = await self._provider.generate(prompt, system)
        logger.debug("LLM RESPONSE (generate): %s", response)
        return response

    async def generate_structured(
        self, prompt: str, schema: dict[str, Any], system: str = ""
    ) -> dict[str, Any]:
        logger.debug(
            "LLM REQUEST (structured): system=%s, schema=%s, prompt=%s",
            system,
            json.dumps(schema),
            prompt,
        )
        response = await self._provider.generate_structured(prompt, schema, system)
        logger.debug("LLM RESPONSE (structured): %s", json.dumps(response))
        return response

    async def generate_stream(
        self, prompt: str, system: str = ""
    ) -> AsyncIterator[str]:
        logger.debug("LLM REQUEST (stream): system=%s, prompt=%s", system, prompt)
        logger.debug("LLM RESPONSE (stream): [STARTING STREAM]")
        full_response = []
        async for chunk in self._provider.generate_stream(prompt, system):
            full_response.append(chunk)
            yield chunk
        logger.debug("LLM RESPONSE (stream): [FINISHED] %s", "".join(full_response))
