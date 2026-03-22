from __future__ import annotations
import json
from typing import Any, AsyncIterator
import anthropic
from worker.llm.base import LLMProvider

class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate(self, prompt: str, system: str = "") -> str:
        kwargs: dict = {"model": self._model, "max_tokens": 8192,
                        "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        response = await self._client.messages.create(**kwargs)
        return response.content[0].text

    async def generate_structured(self, prompt: str, schema: dict[str, Any], system: str = "") -> dict[str, Any]:
        json_prompt = f"{prompt}\n\nRespond ONLY with valid JSON matching this schema:\n{json.dumps(schema)}"
        raw = await self.generate(json_prompt, system=system)
        raw = raw.strip()
        if raw.startswith("```"):
            # Strip opening fence line (may include language tag like ```json)
            raw = raw.split("\n", 1)[1]
            # Strip closing fence
            if "```" in raw:
                raw = raw.rsplit("```", 1)[0]
        return json.loads(raw)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        kwargs: dict = {"model": self._model, "max_tokens": 8192,
                        "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
