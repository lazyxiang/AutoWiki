"""Prompt segment abstraction for cache-aware LLM calls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptSegment:
    text: str
    cacheable: bool = False


PromptInput = str | list[PromptSegment]


def normalize_prompt(prompt: PromptInput) -> list[PromptSegment]:
    """Plain string → [PromptSegment(text=..., cacheable=False)]. List → unchanged."""
    if isinstance(prompt, list):
        return prompt
    return [PromptSegment(text=prompt, cacheable=False)]


def segments_to_text(segments: list[PromptSegment]) -> str:
    """Concatenate all segment texts."""
    return "".join(seg.text for seg in segments)
