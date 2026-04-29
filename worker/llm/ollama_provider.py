from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from worker.llm.base import LLMProvider, _parse_json_response
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text


class OllamaProvider(LLMProvider):
    def __init__(self, model: str = "llama3", base_url: str = "http://localhost:11434"):
        self._model = model
        self._base_url = base_url.rstrip("/")

    async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
        prompt_text = segments_to_text(normalize_prompt(prompt))
        system_text = segments_to_text(normalize_prompt(system))
        payload = {
            "model": self._model,
            "prompt": prompt_text,
            "stream": False,
            "options": {"num_predict": 32000},
        }
        if system_text:
            payload["system"] = system_text
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{self._base_url}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json()["response"]

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
        payload = {
            "model": self._model,
            "prompt": prompt_text,
            "stream": True,
            "options": {"num_predict": 32000},
        }
        if system_text:
            payload["system"] = system_text
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", f"{self._base_url}/api/generate", json=payload
            ) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        data = json.loads(line)
                        yield data.get("response", "")
