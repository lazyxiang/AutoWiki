from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, system: str = "") -> str:
        """Generate text from a prompt. Returns the full response string."""

    @abstractmethod
    async def generate_structured(self, prompt: str, schema: dict[str, Any], system: str = "") -> dict[str, Any]:
        """Generate and parse a JSON response matching the given schema."""

    @abstractmethod
    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        """Async generator that yields text chunks as they arrive."""
