from __future__ import annotations
import json
from typing import Any, AsyncIterator
from worker.llm.base import LLMProvider

try:
    import google.generativeai as genai
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False

class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-1.5-pro"):
        if not _GENAI_AVAILABLE:
            raise ImportError("google-generativeai is not installed. Run: pip install google-generativeai")
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model_name=model)

    async def generate(self, prompt: str, system: str = "") -> str:
        response = await self._model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=8192)
        )
        return response.text

    async def generate_structured(self, prompt: str, schema: dict[str, Any], system: str = "") -> dict[str, Any]:
        response = await self._model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=8192
            )
        )
        return json.loads(response.text)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        response = await self._model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=8192),
            stream=True
        )
        async for chunk in response:
            yield chunk.text
