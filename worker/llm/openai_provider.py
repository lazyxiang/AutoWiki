from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from worker.llm.base import LLMProvider, _parse_json_response
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text


class OpenAIProvider(LLMProvider):
    def __init__(
        self, api_key: str, model: str = "gpt-4o", base_url: str | None = None
    ):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        self._model = model

    async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
        prompt_text = segments_to_text(normalize_prompt(prompt))
        system_text = segments_to_text(normalize_prompt(system))
        messages = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": prompt_text})
        response = await self._client.chat.completions.create(
            model=self._model, messages=messages, max_tokens=16000
        )
        return response.choices[0].message.content

    async def generate_structured(
        self, prompt: PromptInput, schema: dict[str, Any], system: PromptInput = ""
    ) -> dict[str, Any]:
        prompt_text = segments_to_text(normalize_prompt(prompt))
        schema_str = json.dumps(schema)
        json_prompt = (
            f"{prompt_text}\n\nRespond ONLY with valid JSON"
            f" matching this schema:\n{schema_str}"
        )
        raw = await self.generate(json_prompt, system=system)
        return _parse_json_response(raw)

    async def generate_stream(
        self, prompt: PromptInput, system: PromptInput = ""
    ) -> AsyncIterator[str]:
        prompt_text = segments_to_text(normalize_prompt(prompt))
        system_text = segments_to_text(normalize_prompt(system))
        messages = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": prompt_text})
        stream = await self._client.chat.completions.create(
            model=self._model, messages=messages, max_tokens=16000, stream=True
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
