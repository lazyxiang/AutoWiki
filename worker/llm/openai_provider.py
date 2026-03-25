from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from worker.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(
        self, api_key: str, model: str = "gpt-4o", base_url: str | None = None
    ):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        self._model = model

    async def generate(self, prompt: str, system: str = "") -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = await self._client.chat.completions.create(
            model=self._model, messages=messages, max_tokens=8192
        )
        return response.choices[0].message.content

    async def generate_structured(
        self, prompt: str, schema: dict[str, Any], system: str = ""
    ) -> dict[str, Any]:
        schema_str = json.dumps(schema)
        json_prompt = (
            f"{prompt}\n\nRespond ONLY with valid JSON"
            f" matching this schema:\n{schema_str}"
        )
        raw = await self.generate(json_prompt, system=system)
        raw = raw.strip()
        if raw.startswith("```"):
            # Strip opening fence line (may include language tag like ```json)
            raw = raw.split("\n", 1)[1]
            # Strip closing fence
            if "```" in raw:
                raw = raw.rsplit("```", 1)[0]
        return json.loads(raw)

    async def generate_stream(
        self, prompt: str, system: str = ""
    ) -> AsyncIterator[str]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        stream = await self._client.chat.completions.create(
            model=self._model, messages=messages, max_tokens=8192, stream=True
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
