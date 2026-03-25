from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from worker.llm.base import LLMProvider

try:
    from google import genai
    from google.genai import types

    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-1.5-pro"):
        if not _GENAI_AVAILABLE:
            raise ImportError(
                "google-genai is not installed. Run: pip install google-genai"
            )
        self._client = genai.Client(
            api_key=api_key, http_options={"api_version": "v1beta"}
        )
        self._model = model

    async def generate(self, prompt: str, system: str = "") -> str:
        config = types.GenerateContentConfig(
            system_instruction=system if system else None,
            max_output_tokens=8192,
        )
        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=self._model,
            contents=prompt,
            config=config,
        )
        return response.text

    async def generate_structured(
        self, prompt: str, schema: dict[str, Any], system: str = ""
    ) -> dict[str, Any]:
        config = types.GenerateContentConfig(
            system_instruction=system if system else None,
            response_mime_type="application/json",
            max_output_tokens=8192,
        )
        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=self._model,
            contents=prompt,
            config=config,
        )
        return json.loads(response.text)

    async def generate_stream(
        self, prompt: str, system: str = ""
    ) -> AsyncIterator[str]:
        config = types.GenerateContentConfig(
            system_instruction=system if system else None,
            max_output_tokens=8192,
        )

        # Note: google-genai stream is a generator. We iterate
        # in a thread or use its own async if available.
        # For simplicity and correctness with the SDK's current sync nature:
        def sync_stream():
            return self._client.models.generate_content_stream(
                model=self._model,
                contents=prompt,
                config=config,
            )

        stream = await asyncio.to_thread(sync_stream)
        for chunk in stream:
            if chunk.text:
                yield chunk.text
