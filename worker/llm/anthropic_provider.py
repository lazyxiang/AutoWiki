from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from worker.llm.base import LLMProvider, _parse_json_response
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text

_MAX_CACHE_BREAKPOINTS = 4


def _segments_to_anthropic_content(
    prompt: PromptInput,
) -> str | list[dict[str, Any]]:
    """Convert PromptInput to Anthropic message content.

    If no segment is cacheable, returns a plain string (joined text).
    Otherwise returns a list of content blocks, placing cache_control on the
    last segment of each contiguous cacheable run, up to 4 breakpoints total.
    """
    segments = normalize_prompt(prompt)
    if not any(seg.cacheable for seg in segments):
        return segments_to_text(segments)

    blocks: list[dict[str, Any]] = []
    breakpoints_used = 0
    i = 0
    while i < len(segments):
        seg = segments[i]
        if seg.cacheable:
            # Find the end of this contiguous cacheable run
            run_end = i
            while run_end + 1 < len(segments) and segments[run_end + 1].cacheable:
                run_end += 1
            # Emit all segments in this run
            for j in range(i, run_end):
                blocks.append({"type": "text", "text": segments[j].text})
            # Last segment in the run gets cache_control (if under limit)
            last_block: dict[str, Any] = {
                "type": "text",
                "text": segments[run_end].text,
            }
            if breakpoints_used < _MAX_CACHE_BREAKPOINTS:
                last_block["cache_control"] = {"type": "ephemeral"}
                breakpoints_used += 1
            blocks.append(last_block)
            i = run_end + 1
        else:
            blocks.append({"type": "text", "text": seg.text})
            i += 1

    return blocks


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
        content = _segments_to_anthropic_content(prompt)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            kwargs["system"] = _segments_to_anthropic_content(system)
        response = await self._client.messages.create(**kwargs)
        return response.content[0].text

    async def generate_structured(
        self, prompt: PromptInput, schema: dict[str, Any], system: PromptInput = ""
    ) -> dict[str, Any]:
        schema_str = json.dumps(schema)
        prompt_text = segments_to_text(normalize_prompt(prompt))
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
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt_text}],
        }
        if system:
            kwargs["system"] = segments_to_text(normalize_prompt(system))
        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
