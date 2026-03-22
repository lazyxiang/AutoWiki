from __future__ import annotations
import json
from typing import Any, AsyncIterator
from worker.llm.base import LLMProvider

# TODO: migrate to google-genai package (google-generativeai is end-of-life)
# New API: from google import genai; client = genai.Client(api_key=...)
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
        self._model_name = model
        self._model = genai.GenerativeModel(model_name=model)

    def _get_model(self, system: str = ""):
        """Return a model instance, optionally with system_instruction."""
        if system:
            return genai.GenerativeModel(
                model_name=self._model_name,
                system_instruction=system,
            )
        return self._model

    async def generate(self, prompt: str, system: str = "") -> str:
        model = self._get_model(system)
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=8192)
        )
        return response.text

    async def generate_structured(self, prompt: str, schema: dict[str, Any], system: str = "") -> dict[str, Any]:
        model = self._get_model(system)
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=8192
            )
        )
        return json.loads(response.text)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        model = self._get_model(system)
        response = await model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=8192),
            stream=True
        )
        async for chunk in response:
            yield chunk.text
