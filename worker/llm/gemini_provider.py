from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from worker.llm.base import LLMProvider, _parse_json_response
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text

try:
    from google import genai
    from google.genai import errors as _genai_errors
    from google.genai import types

    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False


def _unwrap_genai_error(exc: Exception) -> Exception:
    """Convert Gemini 429 ClientError to TimeoutError so async_retry will retry it."""
    if _GENAI_AVAILABLE and isinstance(exc, _genai_errors.ClientError):
        if getattr(exc, "code", None) == 429:
            return TimeoutError(str(exc))
    return exc


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

    async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
        prompt_text = segments_to_text(normalize_prompt(prompt))
        system_text = segments_to_text(normalize_prompt(system))
        config = types.GenerateContentConfig(
            system_instruction=system_text if system_text else None,
            max_output_tokens=65536,
        )
        try:
            response = await asyncio.to_thread(
                self._client.models.generate_content,
                model=self._model,
                contents=prompt_text,
                config=config,
            )
        except Exception as exc:
            raise _unwrap_genai_error(exc) from exc
        return response.text

    async def generate_structured(
        self, prompt: PromptInput, schema: dict[str, Any], system: PromptInput = ""
    ) -> dict[str, Any]:
        prompt_text = segments_to_text(normalize_prompt(prompt))
        schema_str = json.dumps(schema)
        prompt_with_schema = (
            f"{prompt_text}\n\nRespond ONLY with valid JSON"
            f" matching this schema:\n{schema_str}"
        )
        system_text = segments_to_text(normalize_prompt(system))
        config = types.GenerateContentConfig(
            system_instruction=system_text if system_text else None,
            response_mime_type="application/json",
            max_output_tokens=32000,
        )
        try:
            response = await asyncio.to_thread(
                self._client.models.generate_content,
                model=self._model,
                contents=prompt_with_schema,
                config=config,
            )
        except Exception as exc:
            raise _unwrap_genai_error(exc) from exc
        return _parse_json_response(response.text)

    async def generate_stream(
        self, prompt: PromptInput, system: PromptInput = ""
    ) -> AsyncIterator[str]:
        prompt_text = segments_to_text(normalize_prompt(prompt))
        system_text = segments_to_text(normalize_prompt(system))
        config = types.GenerateContentConfig(
            system_instruction=system_text if system_text else None,
            max_output_tokens=65536,
        )

        # Note: google-genai stream is a generator. We iterate
        # in a thread or use its own async if available.
        # For simplicity and correctness with the SDK's current sync nature:
        def sync_stream():
            return self._client.models.generate_content_stream(
                model=self._model,
                contents=prompt_text,
                config=config,
            )

        try:
            stream = await asyncio.to_thread(sync_stream)
            for chunk in stream:
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            unwrapped = _unwrap_genai_error(exc)
            if unwrapped is exc:
                raise
            raise unwrapped from exc
