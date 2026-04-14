# Wiki Page Quality Redesign — Implementation Plan

> **[COMPLETE]** Implemented and merged (PRs #15 and #17). See `docs/superpowers/specs/2026-04-10-wiki-page-quality-redesign.md` for spec-level implementation notes, including the `cache_ttl: long` stub that was not wired.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-pass wiki page generator with a multi-pass pipeline (outline → draft → fact-check → revision) that produces better-grounded, hallucination-resistant wiki pages with richer Mermaid diagrams, while keeping cost flat via a fast/main model split and prompt caching.

**Architecture:** The `LLMProvider` base class gains a `PromptSegment` abstraction for cache-aware prompts. A new `fast_model` config option creates a second provider instance. The page generator is restructured into four discrete passes with separate prompt builders. The RAG store gains doc/code partitioned retrieval. The wiki planner's Phase 2 moves to the fast model.

**Tech Stack:** Python 3.12, pydantic-settings v2, FAISS, anthropic SDK, openai SDK, google-genai SDK, pytest (asyncio_mode=auto)

---

## File Structure

### New files
- `worker/llm/prompt_segment.py` — `PromptSegment` dataclass and `normalize_prompt()` helper
- `worker/pipeline/page_outline.py` — Pass 1: page outline generation and validation
- `worker/pipeline/page_draft.py` — Pass 2: draft generation with new prompt templates
- `worker/pipeline/fact_check.py` — Pass 3: fact-check against source + Pass 4: targeted revision
- `worker/pipeline/diagram_post_processor.py` — Post-processor ensuring diagram headers/sources
- `tests/worker/test_prompt_segment.py` — Unit tests for PromptSegment translation per provider
- `tests/worker/test_page_outline.py` — Unit tests for outline validation
- `tests/worker/test_page_draft.py` — Unit tests for draft prompt building
- `tests/worker/test_fact_check.py` — Unit tests for fact-check parsing, revision splicing, fallback
- `tests/worker/test_diagram_post_processor.py` — Unit tests for diagram header/source enforcement

### Modified files
- `shared/config.py` — Add `fast_model` and `cache_ttl` to `LLMConfig`
- `worker/llm/base.py` — Update `LLMProvider` signature to accept `str | list[PromptSegment]`
- `worker/llm/anthropic_provider.py` — Translate `PromptSegment` to Anthropic cache-control blocks
- `worker/llm/openai_provider.py` — Concatenate segments in order (auto prefix caching)
- `worker/llm/gemini_provider.py` — Concatenate segments in order (implicit caching)
- `worker/llm/ollama_provider.py` — Concatenate segments, ignore cache markers
- `worker/llm/__init__.py` — Add `make_fast_llm_provider()` factory
- `worker/pipeline/rag_indexer.py` — Add `code_k`/`doc_k` params to `search()` and `multi_search()`
- `worker/pipeline/wiki_planner.py` — Thread `fast_llm` for Phase 2, use `PromptSegment` for caching
- `worker/pipeline/page_generator.py` — Replace single-pass with orchestrator calling the 4 new passes
- `worker/jobs.py` — Construct `fast_llm` at startup, pass both providers through pipeline
- `tests/conftest.py` — Add `mock_fast_llm` fixture
- `tests/worker/test_page_generator.py` — Update to test multi-pass orchestrator
- `tests/worker/test_rag_indexer.py` — Add tests for doc/code partitioned retrieval
- `tests/worker/test_wiki_planner.py` — Add tests for Phase 2 on fast_llm

---

## Task 1: PromptSegment Dataclass and Provider Normalization

**Files:**
- Create: `worker/llm/prompt_segment.py`
- Test: `tests/worker/test_prompt_segment.py`

- [ ] **Step 1: Write failing test for PromptSegment creation and normalize_prompt**

```python
# tests/worker/test_prompt_segment.py
from worker.llm.prompt_segment import PromptSegment, normalize_prompt


def test_prompt_segment_defaults():
    seg = PromptSegment(text="hello")
    assert seg.text == "hello"
    assert seg.cacheable is False


def test_prompt_segment_cacheable():
    seg = PromptSegment(text="context", cacheable=True)
    assert seg.cacheable is True


def test_normalize_prompt_from_string():
    result = normalize_prompt("plain text")
    assert result == [PromptSegment(text="plain text", cacheable=False)]


def test_normalize_prompt_from_list():
    segments = [
        PromptSegment(text="cached", cacheable=True),
        PromptSegment(text="variable"),
    ]
    result = normalize_prompt(segments)
    assert result is segments


def test_normalize_prompt_empty_string():
    result = normalize_prompt("")
    assert result == [PromptSegment(text="", cacheable=False)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/worker/test_prompt_segment.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement PromptSegment and normalize_prompt**

```python
# worker/llm/prompt_segment.py
"""Prompt segment abstraction for cache-aware LLM calls.

Providers translate PromptSegment lists into their native caching
primitives. Passing a plain string is equivalent to passing a single
non-cacheable segment.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptSegment:
    """A segment of a prompt with an optional cache hint.

    Attributes:
        text: The text content of this prompt segment.
        cacheable: When True, providers that support prompt caching
            will mark this segment for caching (e.g. Anthropic's
            cache_control). Providers without caching support
            ignore this flag.
    """

    text: str
    cacheable: bool = False


PromptInput = str | list[PromptSegment]
"""Type alias for prompt parameters that accept either a plain
string or a list of PromptSegment objects."""


def normalize_prompt(prompt: PromptInput) -> list[PromptSegment]:
    """Convert a PromptInput to a list of PromptSegment objects.

    If *prompt* is already a list, returns it unchanged.
    If it is a plain string, wraps it in a single non-cacheable segment.
    """
    if isinstance(prompt, list):
        return prompt
    return [PromptSegment(text=prompt, cacheable=False)]


def segments_to_text(segments: list[PromptSegment]) -> str:
    """Concatenate segment texts into a single string.

    Used by providers that don't support cache markers (OpenAI,
    Ollama) — they simply join all segment texts in order.
    """
    return "".join(seg.text for seg in segments)
```

- [ ] **Step 4: Add test for segments_to_text**

```python
# append to tests/worker/test_prompt_segment.py
from worker.llm.prompt_segment import segments_to_text


def test_segments_to_text():
    segments = [
        PromptSegment(text="System: ", cacheable=True),
        PromptSegment(text="Hello world"),
    ]
    assert segments_to_text(segments) == "System: Hello world"


def test_segments_to_text_empty():
    assert segments_to_text([]) == ""
```

- [ ] **Step 5: Run all tests to verify they pass**

Run: `pytest tests/worker/test_prompt_segment.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add worker/llm/prompt_segment.py tests/worker/test_prompt_segment.py
git commit -m "feat: add PromptSegment dataclass and normalization helpers"
```

---

## Task 2: Update LLMProvider Base Class

**Files:**
- Modify: `worker/llm/base.py`
- Test: `tests/worker/test_prompt_segment.py` (extend)

- [ ] **Step 1: Write failing test for LLMProvider accepting PromptSegment lists**

```python
# append to tests/worker/test_prompt_segment.py
import pytest
from unittest.mock import AsyncMock
from worker.llm.prompt_segment import PromptSegment


async def test_logging_provider_forwards_segment_list():
    """LoggingLLMProvider should forward PromptSegment lists unchanged."""
    from worker.llm.base import LoggingLLMProvider

    inner = AsyncMock()
    inner.generate.return_value = "response"
    provider = LoggingLLMProvider(inner)

    segments = [PromptSegment(text="cached", cacheable=True)]
    result = await provider.generate(segments, system="sys")

    inner.generate.assert_called_once_with(segments, system="sys")
    assert result == "response"


async def test_logging_provider_forwards_string():
    """LoggingLLMProvider should still work with plain strings."""
    from worker.llm.base import LoggingLLMProvider

    inner = AsyncMock()
    inner.generate.return_value = "response"
    provider = LoggingLLMProvider(inner)

    result = await provider.generate("plain prompt", system="sys")
    inner.generate.assert_called_once_with("plain prompt", system="sys")
    assert result == "response"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/worker/test_prompt_segment.py::test_logging_provider_forwards_segment_list -v`
Expected: FAIL — current signature only accepts `str`

- [ ] **Step 3: Update LLMProvider and LoggingLLMProvider signatures**

Modify `worker/llm/base.py`:

Change the type annotations on `generate`, `generate_structured`, and `generate_stream` to accept `str | list[PromptSegment]`. The `generate_batch` method's `prompts` parameter becomes `list[str | list[PromptSegment]]`.

Import at top of file:
```python
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text
```

Update `LLMProvider`:
```python
class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
        """Generate text from a prompt. Returns the full response string."""

    @abstractmethod
    async def generate_structured(
        self, prompt: PromptInput, schema: dict[str, Any], system: PromptInput = ""
    ) -> dict[str, Any]:
        """Generate and parse a JSON response matching the given schema."""

    @abstractmethod
    async def generate_stream(
        self, prompt: PromptInput, system: PromptInput = ""
    ) -> AsyncIterator[str]:
        """Async generator that yields text chunks as they arrive."""

    async def generate_batch(
        self,
        prompts: list[PromptInput],
        system: PromptInput = "",
        max_concurrency: int = 5,
    ) -> list[str]:
        # ... body unchanged except type annotations
```

Update `LoggingLLMProvider` to use `PromptInput` in all signatures and log the text representation via `_truncate(segments_to_text(normalize_prompt(prompt)))` when the input is a segment list.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/worker/test_prompt_segment.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `pytest tests/ --ignore=tests/e2e -v`
Expected: All tests PASS (all existing call sites pass plain strings, which still work)

- [ ] **Step 6: Commit**

```bash
git add worker/llm/base.py worker/llm/prompt_segment.py tests/worker/test_prompt_segment.py
git commit -m "feat: update LLMProvider to accept PromptSegment lists"
```

---

## Task 3: Update Anthropic Provider for Cache-Control

**Files:**
- Modify: `worker/llm/anthropic_provider.py`
- Test: `tests/worker/test_prompt_segment.py` (extend)

- [ ] **Step 1: Write failing test for Anthropic cache-control translation**

```python
# append to tests/worker/test_prompt_segment.py
async def test_anthropic_provider_builds_cache_control_blocks(monkeypatch):
    """AnthropicProvider should translate cacheable PromptSegments to
    content blocks with cache_control markers."""
    import anthropic
    from worker.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="test-model")

    captured_kwargs = {}
    original_create = provider._client.messages.create

    async def mock_create(**kwargs):
        captured_kwargs.update(kwargs)
        # Return a mock response
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.content = [MagicMock(text="response")]
        return resp

    monkeypatch.setattr(provider._client.messages, "create", mock_create)

    segments = [
        PromptSegment(text="cached context", cacheable=True),
        PromptSegment(text="variable tail"),
    ]
    await provider.generate(segments, system="sys")

    # User messages should be a list of content blocks
    messages = captured_kwargs["messages"]
    assert len(messages) == 1
    user_msg = messages[0]
    assert user_msg["role"] == "user"
    content = user_msg["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    # First block is cacheable
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "cached context"
    assert "cache_control" in content[0]
    assert content[0]["cache_control"]["type"] == "ephemeral"
    # Second block is NOT cacheable
    assert content[1]["type"] == "text"
    assert content[1]["text"] == "variable tail"
    assert "cache_control" not in content[1]


async def test_anthropic_provider_plain_string_still_works(monkeypatch):
    """AnthropicProvider should still accept plain strings."""
    from worker.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="test-model")

    captured_kwargs = {}

    async def mock_create(**kwargs):
        captured_kwargs.update(kwargs)
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.content = [MagicMock(text="response")]
        return resp

    monkeypatch.setattr(provider._client.messages, "create", mock_create)

    await provider.generate("plain prompt", system="sys")

    messages = captured_kwargs["messages"]
    assert messages == [{"role": "user", "content": "plain prompt"}]


async def test_anthropic_provider_system_segments(monkeypatch):
    """AnthropicProvider should support cacheable system segments."""
    from worker.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="test-model")

    captured_kwargs = {}

    async def mock_create(**kwargs):
        captured_kwargs.update(kwargs)
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.content = [MagicMock(text="response")]
        return resp

    monkeypatch.setattr(provider._client.messages, "create", mock_create)

    system_segments = [
        PromptSegment(text="You are a writer.", cacheable=True),
        PromptSegment(text="Today's task:"),
    ]
    await provider.generate("prompt", system=system_segments)

    system = captured_kwargs["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"]["type"] == "ephemeral"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/worker/test_prompt_segment.py::test_anthropic_provider_builds_cache_control_blocks -v`
Expected: FAIL

- [ ] **Step 3: Implement cache-control translation in AnthropicProvider**

Modify `worker/llm/anthropic_provider.py`:

```python
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from worker.llm.base import LLMProvider, _parse_json_response
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text


def _segments_to_anthropic_content(
    segments: list,
) -> str | list[dict[str, Any]]:
    """Convert PromptSegment list to Anthropic content blocks.

    If no segment is cacheable, returns a plain string (unchanged behavior).
    Otherwise returns a list of text blocks with cache_control on the last
    segment in each contiguous run of cacheable segments, up to Anthropic's
    4-breakpoint limit.
    """
    from worker.llm.prompt_segment import PromptSegment

    has_cache = any(s.cacheable for s in segments)
    if not has_cache:
        return segments_to_text(segments)

    blocks: list[dict[str, Any]] = []
    cache_breakpoints = 0
    max_breakpoints = 4

    for i, seg in enumerate(segments):
        block: dict[str, Any] = {"type": "text", "text": seg.text}
        if seg.cacheable and cache_breakpoints < max_breakpoints:
            # Place cache_control on this segment if it's the last cacheable
            # in a contiguous run, or simply on every cacheable segment up to limit.
            next_cacheable = (
                i + 1 < len(segments) and segments[i + 1].cacheable
            )
            if not next_cacheable:
                block["cache_control"] = {"type": "ephemeral"}
                cache_breakpoints += 1
        blocks.append(block)

    return blocks


def _segments_to_anthropic_system(
    system: list,
) -> str | list[dict[str, Any]]:
    """Convert system PromptSegment list to Anthropic system parameter."""
    return _segments_to_anthropic_content(system)


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
        segments = normalize_prompt(prompt)
        content = _segments_to_anthropic_content(segments)

        kwargs: dict = {
            "model": self._model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": content}],
        }

        sys_segments = normalize_prompt(system)
        sys_content = _segments_to_anthropic_system(sys_segments)
        if isinstance(sys_content, list):
            kwargs["system"] = sys_content
        elif sys_content:
            kwargs["system"] = sys_content

        response = await self._client.messages.create(**kwargs)
        return response.content[0].text

    async def generate_structured(
        self, prompt: PromptInput, schema: dict[str, Any], system: PromptInput = ""
    ) -> dict[str, Any]:
        schema_str = json.dumps(schema)
        # Append schema instruction to the last segment (or create a new one)
        segments = normalize_prompt(prompt)
        tail = (
            f"\n\nRespond ONLY with valid JSON matching this schema:\n{schema_str}"
        )
        from worker.llm.prompt_segment import PromptSegment
        segments = list(segments) + [PromptSegment(text=tail)]
        raw = await self.generate(segments, system=system)
        return _parse_json_response(raw)

    async def generate_stream(
        self, prompt: PromptInput, system: PromptInput = ""
    ) -> AsyncIterator[str]:
        segments = normalize_prompt(prompt)
        content = _segments_to_anthropic_content(segments)

        kwargs: dict = {
            "model": self._model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": content}],
        }

        sys_segments = normalize_prompt(system)
        sys_content = _segments_to_anthropic_system(sys_segments)
        if isinstance(sys_content, list):
            kwargs["system"] = sys_content
        elif sys_content:
            kwargs["system"] = sys_content

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/worker/test_prompt_segment.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest tests/ --ignore=tests/e2e -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add worker/llm/anthropic_provider.py tests/worker/test_prompt_segment.py
git commit -m "feat: add Anthropic prompt caching via PromptSegment translation"
```

---

## Task 4: Update OpenAI, Gemini, and Ollama Providers

**Files:**
- Modify: `worker/llm/openai_provider.py`
- Modify: `worker/llm/gemini_provider.py`
- Modify: `worker/llm/ollama_provider.py`
- Test: `tests/worker/test_prompt_segment.py` (extend)

- [ ] **Step 1: Write failing tests for all three providers accepting PromptSegment lists**

```python
# append to tests/worker/test_prompt_segment.py
async def test_openai_provider_concatenates_segments(monkeypatch):
    """OpenAI provider should concatenate segment texts in order."""
    from worker.llm.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="test-key", model="test-model")

    captured_kwargs = {}

    async def mock_create(**kwargs):
        captured_kwargs.update(kwargs)
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="ok"))]
        return resp

    monkeypatch.setattr(
        provider._client.chat.completions, "create", mock_create
    )

    segments = [
        PromptSegment(text="cached part", cacheable=True),
        PromptSegment(text=" variable part"),
    ]
    await provider.generate(segments, system="sys")

    messages = captured_kwargs["messages"]
    # System + user
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[1] == {"role": "user", "content": "cached part variable part"}


async def test_ollama_provider_concatenates_segments(monkeypatch):
    """Ollama provider should concatenate segment texts."""
    from worker.llm.ollama_provider import OllamaProvider

    provider = OllamaProvider(model="test", base_url="http://localhost:11434")

    captured_payload = {}

    async def mock_post(url, json=None):
        captured_payload.update(json)
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"response": "ok"}
        return resp

    import httpx
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=mock_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: mock_client)

    segments = [
        PromptSegment(text="cached ", cacheable=True),
        PromptSegment(text="tail"),
    ]
    await provider.generate(segments, system="sys")

    assert captured_payload["prompt"] == "cached tail"
    assert captured_payload["system"] == "sys"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/worker/test_prompt_segment.py::test_openai_provider_concatenates_segments tests/worker/test_prompt_segment.py::test_ollama_provider_concatenates_segments -v`
Expected: FAIL

- [ ] **Step 3: Update OpenAI provider**

Modify `worker/llm/openai_provider.py` — add imports and update all methods to accept `PromptInput`, normalizing to text via `segments_to_text(normalize_prompt(...))`:

```python
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text
```

In `generate()`:
```python
async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
    prompt_text = segments_to_text(normalize_prompt(prompt))
    system_text = segments_to_text(normalize_prompt(system))
    messages = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": prompt_text})
    response = await self._client.chat.completions.create(
        model=self._model, messages=messages, max_tokens=8192
    )
    return response.choices[0].message.content
```

Apply same pattern to `generate_structured` and `generate_stream`.

- [ ] **Step 4: Update Gemini provider**

Modify `worker/llm/gemini_provider.py` — same pattern: import `PromptInput`, normalize and join text. All methods:

```python
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text
```

In `generate()`:
```python
async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
    prompt_text = segments_to_text(normalize_prompt(prompt))
    system_text = segments_to_text(normalize_prompt(system))
    config = types.GenerateContentConfig(
        system_instruction=system_text if system_text else None,
        max_output_tokens=8192,
    )
    response = await asyncio.to_thread(
        self._client.models.generate_content,
        model=self._model,
        contents=prompt_text,
        config=config,
    )
    return response.text
```

Apply same pattern to `generate_structured` and `generate_stream`.

- [ ] **Step 5: Update Ollama provider**

Modify `worker/llm/ollama_provider.py` — same pattern:

```python
from worker.llm.prompt_segment import PromptInput, normalize_prompt, segments_to_text
```

In `generate()`:
```python
async def generate(self, prompt: PromptInput, system: PromptInput = "") -> str:
    prompt_text = segments_to_text(normalize_prompt(prompt))
    system_text = segments_to_text(normalize_prompt(system))
    payload = {"model": self._model, "prompt": prompt_text, "stream": False}
    if system_text:
        payload["system"] = system_text
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{self._base_url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json()["response"]
```

Apply same pattern to `generate_structured` and `generate_stream`.

- [ ] **Step 6: Run all prompt segment tests**

Run: `pytest tests/worker/test_prompt_segment.py -v`
Expected: All tests PASS

- [ ] **Step 7: Run full test suite for regressions**

Run: `pytest tests/ --ignore=tests/e2e -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add worker/llm/openai_provider.py worker/llm/gemini_provider.py worker/llm/ollama_provider.py tests/worker/test_prompt_segment.py
git commit -m "feat: update OpenAI, Gemini, Ollama providers for PromptSegment"
```

---

## Task 5: Fast Model Configuration and Factory

**Files:**
- Modify: `shared/config.py`
- Modify: `worker/llm/__init__.py`
- Test: `tests/worker/test_prompt_segment.py` (extend)

- [ ] **Step 1: Write failing tests for config and factory**

```python
# append to tests/worker/test_prompt_segment.py
def test_llm_config_has_fast_model():
    from shared.config import LLMConfig
    cfg = LLMConfig(provider="anthropic", model="claude-sonnet-4-6")
    # fast_model defaults to empty string (meaning same as model)
    assert cfg.fast_model == ""


def test_llm_config_has_cache_ttl():
    from shared.config import LLMConfig
    cfg = LLMConfig()
    assert cfg.cache_ttl == "short"


def test_llm_config_cache_ttl_long():
    from shared.config import LLMConfig
    cfg = LLMConfig(cache_ttl="long")
    assert cfg.cache_ttl == "long"


def test_make_fast_llm_provider_returns_same_when_no_fast_model(monkeypatch):
    """When fast_model is empty, make_fast_llm_provider returns the same provider."""
    from shared.config import Config
    from worker.llm import make_llm_provider, make_fast_llm_provider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    main = make_llm_provider(cfg)
    fast = make_fast_llm_provider(cfg, main)
    assert fast is main


def test_make_fast_llm_provider_returns_different_when_fast_model_set(monkeypatch):
    """When fast_model is set, make_fast_llm_provider creates a new provider."""
    from shared.config import Config, LLMConfig
    from worker.llm import make_llm_provider, make_fast_llm_provider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config(llm=LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        fast_model="claude-haiku-4-5",
        api_key="test-key",
    ))
    main = make_llm_provider(cfg)
    fast = make_fast_llm_provider(cfg, main)
    assert fast is not main
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/worker/test_prompt_segment.py::test_llm_config_has_fast_model -v`
Expected: FAIL — `fast_model` not yet defined

- [ ] **Step 3: Add fast_model and cache_ttl to LLMConfig**

Modify `shared/config.py`:

```python
class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_LLM_")
    provider: Literal[
        "anthropic", "google", "openai", "openai-compatible", "ollama"
    ] = "anthropic"
    model: str = "claude-sonnet-4-6"
    fast_model: str = ""
    api_key: str = ""
    base_url: str = ""
    cache_ttl: Literal["short", "long"] = "short"
```

- [ ] **Step 4: Add make_fast_llm_provider to factory**

Modify `worker/llm/__init__.py`:

```python
from __future__ import annotations

import os

from worker.llm.base import LLMProvider


def make_llm_provider(cfg) -> LLMProvider:
    """Factory: create LLMProvider from config."""
    from worker.llm.anthropic_provider import AnthropicProvider
    from worker.llm.base import LoggingLLMProvider
    from worker.llm.gemini_provider import GeminiProvider
    from worker.llm.ollama_provider import OllamaProvider
    from worker.llm.openai_provider import OpenAIProvider

    p = cfg.llm.provider
    if p == "anthropic":
        provider = AnthropicProvider(
            api_key=cfg.llm.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            model=cfg.llm.model,
        )
    elif p == "google":
        provider = GeminiProvider(
            api_key=cfg.llm.api_key or os.environ.get("GOOGLE_API_KEY", ""),
            model=cfg.llm.model,
        )
    elif p in ("openai", "openai-compatible"):
        provider = OpenAIProvider(
            api_key=cfg.llm.api_key or os.environ.get("OPENAI_API_KEY", ""),
            model=cfg.llm.model,
            base_url=cfg.llm.base_url or None,
        )
    elif p == "ollama":
        provider = OllamaProvider(
            model=cfg.llm.model,
            base_url=cfg.llm.base_url or "http://localhost:11434",
        )
    else:
        raise ValueError(f"Unknown LLM provider: {p}")

    if cfg.debug or os.environ.get("AUTOWIKI_DEBUG", "").lower() == "true":
        return LoggingLLMProvider(provider)
    return provider


def make_fast_llm_provider(cfg, main_provider: LLMProvider) -> LLMProvider:
    """Create the fast LLM provider for classification/verification tasks.

    If ``cfg.llm.fast_model`` is empty or matches ``cfg.llm.model``,
    returns *main_provider* directly (single-model mode).
    Otherwise creates a new provider instance with the fast model name.
    """
    fast_model = cfg.llm.fast_model
    if not fast_model or fast_model == cfg.llm.model:
        return main_provider

    from worker.llm.anthropic_provider import AnthropicProvider
    from worker.llm.base import LoggingLLMProvider
    from worker.llm.gemini_provider import GeminiProvider
    from worker.llm.ollama_provider import OllamaProvider
    from worker.llm.openai_provider import OpenAIProvider

    p = cfg.llm.provider
    if p == "anthropic":
        provider = AnthropicProvider(
            api_key=cfg.llm.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            model=fast_model,
        )
    elif p == "google":
        provider = GeminiProvider(
            api_key=cfg.llm.api_key or os.environ.get("GOOGLE_API_KEY", ""),
            model=fast_model,
        )
    elif p in ("openai", "openai-compatible"):
        provider = OpenAIProvider(
            api_key=cfg.llm.api_key or os.environ.get("OPENAI_API_KEY", ""),
            model=fast_model,
            base_url=cfg.llm.base_url or None,
        )
    elif p == "ollama":
        provider = OllamaProvider(
            model=fast_model,
            base_url=cfg.llm.base_url or "http://localhost:11434",
        )
    else:
        raise ValueError(f"Unknown LLM provider: {p}")

    if cfg.debug or os.environ.get("AUTOWIKI_DEBUG", "").lower() == "true":
        return LoggingLLMProvider(provider)
    return provider
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/worker/test_prompt_segment.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ --ignore=tests/e2e -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add shared/config.py worker/llm/__init__.py tests/worker/test_prompt_segment.py
git commit -m "feat: add fast_model and cache_ttl config, fast LLM factory"
```

---

## Task 6: RAG Store Documentation Downweighting

**Files:**
- Modify: `worker/pipeline/rag_indexer.py`
- Test: `tests/worker/test_rag_indexer.py` (extend)

- [ ] **Step 1: Write failing tests for code_k/doc_k partitioned retrieval**

```python
# append to tests/worker/test_rag_indexer.py
import tempfile
from pathlib import Path

import numpy as np

from worker.pipeline.rag_indexer import FAISSStore


def _make_store_with_docs_and_code():
    """Create a store with 3 code chunks and 2 doc chunks."""
    tmpdir = tempfile.mkdtemp()
    store = FAISSStore(
        dimension=4,
        index_path=Path(tmpdir) / "idx",
        meta_path=Path(tmpdir) / "meta.pkl",
    )
    # Use orthogonal-ish vectors so we can control ranking
    vecs = [
        np.array([1, 0, 0, 0], dtype=np.float32),  # code chunk 1
        np.array([0, 1, 0, 0], dtype=np.float32),  # code chunk 2
        np.array([0, 0, 1, 0], dtype=np.float32),  # code chunk 3
        np.array([0.9, 0.1, 0, 0], dtype=np.float32),  # doc chunk 1 (similar to code 1)
        np.array([0, 0, 0, 1], dtype=np.float32),  # doc chunk 2
    ]
    metas = [
        {"file": "src/main.py", "start_line": 1, "end_line": 10, "text": "code1"},
        {"file": "src/utils.py", "start_line": 1, "end_line": 10, "text": "code2"},
        {"file": "src/models.py", "start_line": 1, "end_line": 10, "text": "code3"},
        {"file": "docs/DESIGN.md", "start_line": 1, "end_line": 10, "text": "doc1"},
        {"file": "README.md", "start_line": 1, "end_line": 10, "text": "doc2"},
    ]
    store.add(vecs, metas)
    return store


def test_search_with_doc_k_caps_docs():
    store = _make_store_with_docs_and_code()
    # Query similar to code chunk 1 and doc chunk 1
    query = np.array([0.95, 0.05, 0, 0], dtype=np.float32)
    results = store.search(query, k=5, doc_k=1)

    doc_results = [r for r in results if r["file"].endswith((".md", ".rst", ".txt", ".adoc"))]
    code_results = [r for r in results if not r["file"].endswith((".md", ".rst", ".txt", ".adoc"))]

    assert len(doc_results) <= 1
    assert len(code_results) <= 4  # k - doc_k = 4


def test_search_with_doc_k_zero_excludes_all_docs():
    store = _make_store_with_docs_and_code()
    query = np.array([0.95, 0.05, 0, 0], dtype=np.float32)
    results = store.search(query, k=5, doc_k=0)

    for r in results:
        assert not r["file"].endswith((".md", ".rst", ".txt", ".adoc"))


def test_search_without_doc_k_returns_all(monkeypatch):
    """Default behavior (doc_k=None) should return all results unpartitioned."""
    store = _make_store_with_docs_and_code()
    query = np.array([0.95, 0.05, 0, 0], dtype=np.float32)
    results = store.search(query, k=5)
    # Should return up to 5 results without partitioning
    assert len(results) == 5


def test_multi_search_with_doc_k():
    store = _make_store_with_docs_and_code()
    q1 = np.array([0.95, 0.05, 0, 0], dtype=np.float32)
    q2 = np.array([0, 0, 0.9, 0.1], dtype=np.float32)
    results = store.multi_search([q1, q2], k=5, doc_k=1)

    doc_results = [r for r in results if r["file"].endswith((".md", ".rst", ".txt", ".adoc"))]
    assert len(doc_results) <= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/worker/test_rag_indexer.py::test_search_with_doc_k_caps_docs -v`
Expected: FAIL — `search()` doesn't accept `doc_k`

- [ ] **Step 3: Implement doc/code partitioning in search() and multi_search()**

Modify `worker/pipeline/rag_indexer.py`. Add a module-level constant and update both methods:

```python
_DOC_EXTENSIONS = frozenset({".md", ".rst", ".txt", ".adoc"})


def _is_doc_chunk(meta: dict[str, Any]) -> bool:
    """Return True if this chunk comes from a documentation file."""
    file_path = meta.get("file", "")
    return Path(file_path).suffix.lower() in _DOC_EXTENSIONS
```

In `search()` — add `doc_k: int | None = None` parameter. After the existing FAISS search, if `doc_k is not None`, partition results into code and doc, take up to `code_k = k - doc_k` code chunks and up to `doc_k` doc chunks, interleave code-first:

```python
def search(
    self, query: np.ndarray, k: int = 5, doc_k: int | None = None
) -> list[dict[str, Any]]:
    self._ensure_index()
    if self._index.ntotal == 0:
        return []
    q = query.astype(np.float32).reshape(1, -1)
    faiss.normalize_L2(q)
    # Retrieve extra candidates to fill both buckets after partitioning
    fetch_k = min(k * 2 if doc_k is not None else k, self._index.ntotal)
    _, indices = self._index.search(q, fetch_k)
    all_results = [self._metas[i] for i in indices[0] if i >= 0]

    if doc_k is None:
        return all_results[:k]

    code_k = k - doc_k
    code_chunks = []
    doc_chunks = []
    for meta in all_results:
        if _is_doc_chunk(meta):
            if len(doc_chunks) < doc_k:
                doc_chunks.append(meta)
        else:
            if len(code_chunks) < code_k:
                code_chunks.append(meta)
        if len(code_chunks) >= code_k and len(doc_chunks) >= doc_k:
            break

    return code_chunks + doc_chunks
```

In `multi_search()` — add `doc_k: int | None = None` parameter. Apply the same partitioning logic at the end after deduplication:

```python
def multi_search(
    self, queries: list[np.ndarray], k: int = 5, doc_k: int | None = None
) -> list[dict[str, Any]]:
    self._ensure_index()
    if self._index.ntotal == 0:
        return []

    seen_keys: set[tuple] = set()
    results: list[dict[str, Any]] = []

    for query in queries:
        q = query.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(q)
        actual_k = min(k, self._index.ntotal)
        _, indices = self._index.search(q, actual_k)
        for i in indices[0]:
            if i < 0:
                continue
            meta = self._metas[i]
            dedup_key = (
                meta.get("file", ""),
                meta.get("start_line", 0),
                meta.get("chunk_idx", i),
            )
            if dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                results.append(meta)

    if doc_k is None:
        return results

    code_k = k - doc_k
    code_chunks = []
    doc_chunks = []
    for meta in results:
        if _is_doc_chunk(meta):
            if len(doc_chunks) < doc_k:
                doc_chunks.append(meta)
        else:
            if len(code_chunks) < code_k:
                code_chunks.append(meta)

    return code_chunks + doc_chunks
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/worker/test_rag_indexer.py -v`
Expected: All tests PASS (new and existing)

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ --ignore=tests/e2e -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/rag_indexer.py tests/worker/test_rag_indexer.py
git commit -m "feat: add doc/code partitioned retrieval to FAISSStore"
```

---

## Task 7: Page Outline Pass (Pass 1)

**Files:**
- Create: `worker/pipeline/page_outline.py`
- Test: `tests/worker/test_page_outline.py`

- [ ] **Step 1: Write failing tests for outline schema, validation, and generation**

```python
# tests/worker/test_page_outline.py
import pytest
from worker.pipeline.page_outline import (
    VALID_DIAGRAM_TYPES,
    VALID_SECTION_KINDS,
    PageOutline,
    validate_outline,
)


def test_valid_outline_passes_validation():
    raw = {
        "sections": [
            {"heading": "Overview", "kind": "prose", "focus": "What it does", "diagram": None},
            {
                "heading": "Architecture",
                "kind": "prose+diagram",
                "focus": "How it works",
                "diagram": {
                    "type": "flowchart",
                    "purpose": "Show data flow",
                    "source_files": ["src/main.py"],
                },
            },
            {
                "heading": "API Surface",
                "kind": "prose+table",
                "focus": "Public methods",
                "diagram": {
                    "type": "classDiagram",
                    "purpose": "Class relationships",
                    "source_files": ["src/models.py"],
                },
            },
        ],
        "key_claims": [
            "FAISSStore uses IndexFlatIP",
            "multi_search deduplicates by (file, start_line)",
            "Chunk size defaults to 1000",
        ],
    }
    outline = validate_outline(raw, page_files=["src/main.py", "src/models.py"])
    assert isinstance(outline, PageOutline)
    assert len(outline.sections) == 3
    assert len(outline.key_claims) == 3


def test_invalid_kind_rejected():
    raw = {
        "sections": [
            {"heading": "X", "kind": "invalid_kind", "focus": "f", "diagram": None},
        ],
        "key_claims": ["claim1", "claim2", "claim3"],
    }
    with pytest.raises(ValueError, match="kind"):
        validate_outline(raw, page_files=[])


def test_invalid_diagram_type_rejected():
    raw = {
        "sections": [
            {
                "heading": "X",
                "kind": "prose",
                "focus": "f",
                "diagram": {
                    "type": "pieDiagram",
                    "purpose": "p",
                    "source_files": ["a.py"],
                },
            },
        ],
        "key_claims": ["claim1", "claim2", "claim3"],
    }
    with pytest.raises(ValueError, match="diagram type"):
        validate_outline(raw, page_files=["a.py"])


def test_too_few_claims_rejected():
    raw = {
        "sections": [
            {"heading": "X", "kind": "prose", "focus": "f", "diagram": None},
        ],
        "key_claims": ["only one"],
    }
    with pytest.raises(ValueError, match="key_claims"):
        validate_outline(raw, page_files=[])


def test_too_many_claims_rejected():
    raw = {
        "sections": [
            {"heading": "X", "kind": "prose", "focus": "f", "diagram": None},
        ],
        "key_claims": [f"claim {i}" for i in range(10)],
    }
    with pytest.raises(ValueError, match="key_claims"):
        validate_outline(raw, page_files=[])


def test_diagram_source_files_must_be_subset():
    raw = {
        "sections": [
            {
                "heading": "X",
                "kind": "prose",
                "focus": "f",
                "diagram": {
                    "type": "flowchart",
                    "purpose": "p",
                    "source_files": ["unknown.py"],
                },
            },
        ],
        "key_claims": ["a", "b", "c"],
    }
    with pytest.raises(ValueError, match="source_files"):
        validate_outline(raw, page_files=["src/main.py"])


def test_minimum_diagram_count_enforced():
    """Pages with >=3 sections must have >=2 diagrams."""
    raw = {
        "sections": [
            {"heading": "A", "kind": "prose", "focus": "a", "diagram": None},
            {"heading": "B", "kind": "prose", "focus": "b", "diagram": None},
            {"heading": "C", "kind": "prose", "focus": "c", "diagram": None},
        ],
        "key_claims": ["a", "b", "c"],
    }
    with pytest.raises(ValueError, match="diagram"):
        validate_outline(raw, page_files=[])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/worker/test_page_outline.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement page_outline.py**

```python
# worker/pipeline/page_outline.py
"""Pass 1 of the multi-pass page generator — structured page outline.

Produces a JSON outline (sections, planned diagrams, key claims) that
guides the draft pass and targets the fact-check pass.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptInput, PromptSegment
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

if TYPE_CHECKING:
    from worker.pipeline.wiki_planner import WikiPageSpec

logger = logging.getLogger("worker.page_outline")

VALID_SECTION_KINDS = frozenset({
    "prose",
    "prose+table",
    "prose+list",
    "prose+diagram",
    "prose+table+diagram",
})

VALID_DIAGRAM_TYPES = frozenset({
    "flowchart",
    "flowchart TD",
    "flowchart LR",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "journey",
    "gantt",
    "mindmap",
    "graph LR",
})

_OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "kind": {"type": "string"},
                    "focus": {"type": "string"},
                    "diagram": {
                        "type": ["object", "null"],
                        "properties": {
                            "type": {"type": "string"},
                            "purpose": {"type": "string"},
                            "source_files": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["heading", "kind", "focus"],
            },
        },
        "key_claims": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["sections", "key_claims"],
}


@dataclass
class DiagramPlan:
    """Plan for a single diagram within a page section."""

    type: str
    purpose: str
    source_files: list[str] = field(default_factory=list)


@dataclass
class SectionPlan:
    """Plan for a single section within the page outline."""

    heading: str
    kind: str
    focus: str
    diagram: DiagramPlan | None = None


@dataclass
class PageOutline:
    """The validated outline for a single wiki page."""

    sections: list[SectionPlan]
    key_claims: list[str]


def validate_outline(
    raw: dict[str, Any],
    page_files: list[str],
) -> PageOutline:
    """Validate an LLM-produced outline dict and return a PageOutline.

    Raises:
        ValueError: On any validation failure with a descriptive message.
    """
    sections_raw = raw.get("sections", [])
    if not sections_raw:
        raise ValueError("Outline must have at least one section")

    claims = raw.get("key_claims", [])
    if len(claims) < 3 or len(claims) > 8:
        raise ValueError(
            f"key_claims must contain 3-8 items, got {len(claims)}"
        )

    page_files_set = set(page_files)
    sections: list[SectionPlan] = []
    diagram_count = 0

    for s in sections_raw:
        heading = s.get("heading", "")
        kind = s.get("kind", "")
        focus = s.get("focus", "")

        if not heading:
            raise ValueError("Section missing heading")
        if kind not in VALID_SECTION_KINDS:
            raise ValueError(
                f"Invalid section kind '{kind}' for '{heading}'. "
                f"Must be one of: {', '.join(sorted(VALID_SECTION_KINDS))}"
            )

        diagram = None
        diagram_raw = s.get("diagram")
        if diagram_raw:
            diag_type = diagram_raw.get("type", "")
            if diag_type not in VALID_DIAGRAM_TYPES:
                raise ValueError(
                    f"Invalid diagram type '{diag_type}' in section '{heading}'. "
                    f"Must be one of: {', '.join(sorted(VALID_DIAGRAM_TYPES))}"
                )
            source_files = diagram_raw.get("source_files", [])
            if page_files_set and source_files:
                invalid = [f for f in source_files if f not in page_files_set]
                if invalid:
                    raise ValueError(
                        f"diagram source_files {invalid} not in page's assigned "
                        f"files for section '{heading}'"
                    )
            diagram = DiagramPlan(
                type=diag_type,
                purpose=diagram_raw.get("purpose", ""),
                source_files=source_files,
            )
            diagram_count += 1

        sections.append(SectionPlan(
            heading=heading, kind=kind, focus=focus, diagram=diagram
        ))

    # Minimum diagram count rule
    if len(sections) >= 3 and diagram_count < 2:
        raise ValueError(
            f"Pages with {len(sections)} sections must have at least 2 diagrams, "
            f"got {diagram_count}"
        )
    if diagram_count < 1:
        raise ValueError("Every page must produce at least 1 diagram")

    return PageOutline(sections=sections, key_claims=claims)


_SYSTEM = (
    "You are a senior technical writer planning the structure of a wiki page. "
    "Given information about a software component — its files, entities, and "
    "dependencies — produce a structured JSON outline that will guide the "
    "actual page drafting.\n\n"
    "Rules:\n"
    "- Choose diagram types that best fit the content from this palette:\n"
    "  flowchart, flowchart TD, flowchart LR, sequenceDiagram, classDiagram,\n"
    "  stateDiagram-v2, erDiagram, journey, gantt, mindmap, graph LR\n"
    "- diagram.source_files must be files assigned to THIS page\n"
    "- key_claims must be concrete, verifiable against source code\n"
    "- Output ONLY valid JSON"
)


def _build_outline_prompt(
    spec: WikiPageSpec,
    entity_summaries: str,
    dep_info: str | None,
    child_titles: list[str] | None = None,
) -> list[PromptSegment]:
    """Build the outline prompt with cacheable entity context."""
    # Cacheable prefix: entity summaries + dep info (reused by fact-check)
    cached_parts = [f"Page: {spec.title}\nPurpose: {spec.purpose}\n"]
    cached_parts.append(f"Assigned files: {', '.join(spec.files or [])}\n")
    cached_parts.append(f"Entity summaries:\n{entity_summaries}\n")
    if dep_info:
        cached_parts.append(f"Dependencies:\n{dep_info}\n")
    if child_titles:
        cached_parts.append(
            f"Child pages: {', '.join(child_titles)}\n"
        )

    # Variable tail: schema + instructions
    schema_json = json.dumps(_OUTLINE_SCHEMA, indent=2)
    n_sections = max(3, len((spec.files or [])) // 2)
    min_diagrams = 2 if n_sections >= 3 else 1
    tail = (
        f"Create an outline with {n_sections}-{n_sections + 3} sections.\n"
        f"Include at least {min_diagrams} diagrams.\n"
        f"Provide 3-8 key_claims — concrete statements verifiable against source.\n\n"
        f"Output JSON matching this schema:\n{schema_json}"
    )

    return [
        PromptSegment(text="\n".join(cached_parts), cacheable=True),
        PromptSegment(text=tail),
    ]


async def generate_page_outline(
    spec: WikiPageSpec,
    entity_summaries: str,
    dep_info: str | None,
    fast_llm: LLMProvider,
    on_retry: OnRetryCallback | None = None,
    max_retries: int = 2,
    child_titles: list[str] | None = None,
    wiki_language: str = "en",
) -> PageOutline:
    """Generate and validate a page outline using the fast model.

    Retries up to *max_retries* times on validation failure, appending
    the error to the prompt.
    """
    from worker.pipeline.language import get_language_instruction

    segments = _build_outline_prompt(spec, entity_summaries, dep_info, child_titles)
    system = _SYSTEM + get_language_instruction(wiki_language)

    for attempt in range(max_retries + 1):
        try:
            raw = await async_retry(
                fast_llm.generate_structured,
                segments,
                schema=_OUTLINE_SCHEMA,
                system=system,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
            return validate_outline(raw, page_files=spec.files or [])
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries:
                error_seg = PromptSegment(
                    text=f"\n\nPrevious attempt failed: {e}. Fix and retry."
                )
                segments = list(segments) + [error_seg]
            else:
                logger.warning(
                    "Outline generation failed after %d retries for '%s': %s",
                    max_retries + 1,
                    spec.title,
                    e,
                )
                raise
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/worker/test_page_outline.py -v`
Expected: All tests PASS

- [ ] **Step 5: Write integration test for generate_page_outline**

```python
# append to tests/worker/test_page_outline.py
from unittest.mock import AsyncMock
from worker.pipeline.wiki_planner import WikiPageSpec


async def test_generate_page_outline_with_mock_llm():
    from worker.pipeline.page_outline import generate_page_outline

    mock_fast_llm = AsyncMock()
    mock_fast_llm.generate_structured.return_value = {
        "sections": [
            {"heading": "Overview", "kind": "prose", "focus": "What it does", "diagram": None},
            {
                "heading": "Architecture",
                "kind": "prose+diagram",
                "focus": "How components connect",
                "diagram": {
                    "type": "flowchart",
                    "purpose": "Component relationships",
                    "source_files": ["src/main.py"],
                },
            },
        ],
        "key_claims": [
            "Main function parses arguments",
            "Config loaded from env vars",
            "Server starts on port 3000",
        ],
    }

    spec = WikiPageSpec(
        title="Core Server",
        purpose="Main server entry point.",
        files=["src/main.py", "src/config.py"],
    )
    outline = await generate_page_outline(
        spec,
        entity_summaries="- function main()\n- class Config",
        dep_info="src/main.py -> src/config.py",
        fast_llm=mock_fast_llm,
    )
    assert len(outline.sections) == 2
    assert outline.sections[1].diagram is not None
    assert outline.sections[1].diagram.type == "flowchart"
    assert len(outline.key_claims) == 3


async def test_generate_page_outline_retries_on_validation_error():
    from worker.pipeline.page_outline import generate_page_outline

    call_count = 0

    async def structured_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"sections": [], "key_claims": []}  # invalid: empty sections
        return {
            "sections": [
                {
                    "heading": "Overview",
                    "kind": "prose",
                    "focus": "f",
                    "diagram": {
                        "type": "flowchart",
                        "purpose": "p",
                        "source_files": ["a.py"],
                    },
                },
            ],
            "key_claims": ["a", "b", "c"],
        }

    mock_fast_llm = AsyncMock()
    mock_fast_llm.generate_structured.side_effect = structured_side_effect

    spec = WikiPageSpec(title="Test", purpose="Test.", files=["a.py"])
    outline = await generate_page_outline(
        spec,
        entity_summaries="entities",
        dep_info=None,
        fast_llm=mock_fast_llm,
        max_retries=2,
    )
    assert call_count == 2
    assert len(outline.sections) == 1
```

- [ ] **Step 6: Run all outline tests**

Run: `pytest tests/worker/test_page_outline.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add worker/pipeline/page_outline.py tests/worker/test_page_outline.py
git commit -m "feat: add page outline pass (Pass 1) with validation"
```

---

## Task 8: Page Draft Pass (Pass 2)

**Files:**
- Create: `worker/pipeline/page_draft.py`
- Test: `tests/worker/test_page_draft.py`

- [ ] **Step 1: Write failing tests for draft prompt building and system prompt**

```python
# tests/worker/test_page_draft.py
from worker.pipeline.page_draft import (
    DRAFT_SYSTEM,
    build_draft_prompt,
)
from worker.pipeline.page_outline import DiagramPlan, PageOutline, SectionPlan
from worker.pipeline.wiki_planner import WikiPageSpec


def test_draft_system_forbids_code_blocks():
    assert "fenced code block" in DRAFT_SYSTEM.lower() or "```python" in DRAFT_SYSTEM or "Never embed" in DRAFT_SYSTEM


def test_draft_system_mentions_mermaid_palette():
    for diagram_type in ["sequenceDiagram", "stateDiagram-v2", "erDiagram", "journey", "mindmap"]:
        assert diagram_type in DRAFT_SYSTEM


def test_draft_system_mentions_source_of_truth():
    assert "source code" in DRAFT_SYSTEM.lower()
    assert "canonical" in DRAFT_SYSTEM.lower() or "source of truth" in DRAFT_SYSTEM.lower()


def test_build_draft_prompt_returns_segments():
    outline = PageOutline(
        sections=[
            SectionPlan(heading="Overview", kind="prose", focus="What it does"),
            SectionPlan(
                heading="Flow",
                kind="prose+diagram",
                focus="Request flow",
                diagram=DiagramPlan(type="sequenceDiagram", purpose="API flow", source_files=["api.py"]),
            ),
        ],
        key_claims=["Claim 1", "Claim 2", "Claim 3"],
    )
    spec = WikiPageSpec(title="API Layer", purpose="HTTP endpoints.", files=["api.py"])
    context_chunks = [
        {"file": "api.py", "start_line": 1, "end_line": 20, "text": "code content"}
    ]
    entity_details = [
        {"type": "function", "name": "list_repos", "file": "api.py", "start_line": 5}
    ]

    segments = build_draft_prompt(
        spec=spec,
        outline=outline,
        context_chunks=context_chunks,
        repo_name="test/repo",
        dep_info=None,
        entity_details=entity_details,
        child_contents=None,
    )

    # Should return list of PromptSegments
    from worker.llm.prompt_segment import PromptSegment
    assert all(isinstance(s, PromptSegment) for s in segments)

    # First segment (source chunks) should be cacheable
    assert segments[0].cacheable is True

    # Full text should contain the outline JSON
    full_text = "".join(s.text for s in segments)
    assert "sequenceDiagram" in full_text
    assert "API Layer" in full_text
    assert "api.py" in full_text


def test_build_draft_prompt_for_parent_page():
    from worker.pipeline.page_generator import PageResult

    outline = PageOutline(
        sections=[
            SectionPlan(heading="Overview", kind="prose", focus="What it does"),
            SectionPlan(
                heading="Subsystem Map",
                kind="prose+diagram",
                focus="How children relate",
                diagram=DiagramPlan(type="mindmap", purpose="Component map", source_files=[]),
            ),
        ],
        key_claims=["Workers connect via Redis", "API is stateless", "Frontend is SPA"],
    )
    spec = WikiPageSpec(title="Architecture", purpose="Top-level.", files=[])
    child_contents = [
        PageResult(slug="api", title="API Layer", content="## API content"),
        PageResult(slug="worker", title="Worker", content="## Worker content"),
    ]

    segments = build_draft_prompt(
        spec=spec,
        outline=outline,
        context_chunks=[],
        repo_name="test/repo",
        dep_info=None,
        entity_details=None,
        child_contents=child_contents,
    )

    full_text = "".join(s.text for s in segments)
    assert "Child Pages" in full_text
    assert "API Layer" in full_text
    assert "SYNTHESIZE" in full_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/worker/test_page_draft.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement page_draft.py**

```python
# worker/pipeline/page_draft.py
"""Pass 2 of the multi-pass page generator — draft generation.

Takes a validated PageOutline plus retrieved source chunks and produces
the full Markdown page via the main LLM model.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.page_generator import PageResult, _format_context_chunks, _format_entity_details
from worker.pipeline.page_outline import PageOutline
from worker.utils.mermaid import sanitize_mermaid_blocks
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

if TYPE_CHECKING:
    from worker.pipeline.wiki_planner import WikiPageSpec

DRAFT_SYSTEM = (
    "You are a senior technical writer creating comprehensive, "
    "production-quality wiki documentation for a software repository.\n\n"
    "Source of truth: the source code provided below is canonical. Any "
    "documentation excerpt (files ending in .md, .rst, etc.) may be out "
    "of date and must be treated as a hint, not a fact. When documentation "
    "and code disagree, trust the code. Never cite a documentation file as "
    "the source of a technical claim — cite the code file that actually "
    "implements the behavior.\n\n"
    "Rules:\n"
    "- Every technical claim MUST be grounded in the provided source code\n"
    "- Never embed fenced code blocks (no ```python, ```js, etc.). The ONLY "
    "fenced blocks allowed are ```mermaid for diagrams\n"
    "- Short inline identifiers like `ClassName.method()` or `MAX_RETRIES = 3` "
    "ARE permitted — these describe the API surface, not code excerpts\n"
    "- After each major section or subsection, add a source annotation in "
    "italics: *Source: path/to/file.py:10-45*\n"
    "- Use tables and bulleted lists for enumerating options, fields, parameters, "
    "or comparisons. Use prose for narrative and design rationale.\n"
    "- Choose diagram types that best fit the content from this palette:\n"
    "  flowchart TD, flowchart LR, sequenceDiagram, classDiagram,\n"
    "  stateDiagram-v2, erDiagram, journey, gantt, mindmap, graph LR\n"
    "- Every diagram MUST be preceded by: **Diagram: <one-line header>**\n"
    "  and followed by: *Source: <file_path>:<start_line>-<end_line>*\n"
    "- IMPORTANT Mermaid quoting rules — violating these causes parse errors:\n"
    '  - Node labels with special chars: A["Server (HTTP)"] not A[Server (HTTP)]\n'
    '  - Edge labels with special chars: -->|"GET /api/{id}"| not -->|GET /api/{id}|\n'
    "  - Special characters requiring quotes: ( ) { } | < > /\n"
    "- Write for developers who are new to this codebase but experienced programmers\n"
    "- Organize content from high-level concepts down to implementation details"
)


def build_draft_prompt(
    spec: WikiPageSpec,
    outline: PageOutline,
    context_chunks: list[dict],
    repo_name: str,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
    child_contents: list[PageResult] | None = None,
) -> list[PromptSegment]:
    """Build the draft prompt as a list of PromptSegments with caching.

    The first segment (source chunks + entity details) is cacheable and
    reused by the fact-check and revision passes on the main model.
    The second segment (outline + instructions) is the variable tail.
    """
    # ── Cacheable prefix: source context ──
    cached_parts = [f"Repository: {repo_name}\nPage: {spec.title}\n"]

    if spec.purpose:
        cached_parts.append(f"Purpose: {spec.purpose}\n")

    cached_parts.append(f"Source files: {', '.join(spec.files or [])}\n")

    # Dependency context
    if dep_info:
        deps_on = dep_info.get("depends_on", [])
        deps_by = dep_info.get("depended_by", [])
        ext_deps = dep_info.get("external_deps", [])
        dep_lines = []
        if deps_on:
            dep_lines.append(f"- Depends on: {', '.join(deps_on)}")
        if deps_by:
            dep_lines.append(f"- Depended on by: {', '.join(deps_by)}")
        if ext_deps:
            dep_lines.append(f"- External dependencies: {', '.join(ext_deps[:10])}")
        if dep_lines:
            cached_parts.append("Dependencies:\n" + "\n".join(dep_lines) + "\n")

    # Entity details
    if entity_details:
        cached_parts.append(
            f"Key entities:\n{_format_entity_details(entity_details)}\n"
        )

    # Source code context
    context = _format_context_chunks(context_chunks)
    cached_parts.append(
        f"Relevant source code (with file paths and line numbers):\n{context}\n"
    )

    # Child page content for parent pages
    if child_contents:
        child_sections = []
        for child in child_contents:
            child_sections.append(f'### Child: "{child.title}"\n{child.content}')
        cached_parts.append(
            "## Child Pages (already generated)\n"
            "The following child pages have been written. Your role is to "
            "SYNTHESIZE and CONNECT — provide the high-level narrative, "
            "explain how these components relate, and add context that "
            "individual pages cannot provide. Do NOT repeat details covered "
            "in child pages; reference them instead.\n\n" + "\n\n".join(child_sections)
            + "\n"
        )

    # ── Variable tail: outline + instructions ──
    outline_json = json.dumps(
        {
            "sections": [
                {
                    "heading": s.heading,
                    "kind": s.kind,
                    "focus": s.focus,
                    "diagram": (
                        {
                            "type": s.diagram.type,
                            "purpose": s.diagram.purpose,
                            "source_files": s.diagram.source_files,
                        }
                        if s.diagram
                        else None
                    ),
                }
                for s in outline.sections
            ],
        },
        indent=2,
    )

    has_children = bool(child_contents)

    if has_children:
        instruction = (
            f'Write a wiki page for "{spec.title}" that serves as the entry point '
            "for its child pages. Follow the outline below exactly.\n"
            "Do NOT duplicate content from child pages — reference them by name.\n"
        )
    else:
        instruction = (
            f'Write a comprehensive wiki page for "{spec.title}". '
            "Follow the outline below exactly.\n"
        )

    tail = (
        f"{instruction}\n"
        f"Page outline:\n{outline_json}\n\n"
        "For each section, write the content matching its 'kind':\n"
        "- prose: narrative paragraphs\n"
        "- prose+table: narrative with a summarizing table\n"
        "- prose+list: narrative with a bulleted list\n"
        "- prose+diagram: narrative with a Mermaid diagram matching the planned type\n"
        "- prose+table+diagram: narrative with both table and diagram\n\n"
        "Output Markdown only."
    )

    return [
        PromptSegment(text="\n".join(cached_parts), cacheable=True),
        PromptSegment(text=tail),
    ]


async def generate_draft(
    spec: WikiPageSpec,
    outline: PageOutline,
    context_chunks: list[dict],
    repo_name: str,
    llm: LLMProvider,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
    child_contents: list[PageResult] | None = None,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
) -> str:
    """Generate the draft Markdown for a wiki page using the main model."""
    from worker.pipeline.language import get_language_instruction

    segments = build_draft_prompt(
        spec=spec,
        outline=outline,
        context_chunks=context_chunks,
        repo_name=repo_name,
        dep_info=dep_info,
        entity_details=entity_details,
        child_contents=child_contents,
    )
    system = DRAFT_SYSTEM + get_language_instruction(wiki_language)

    content = await async_retry(
        llm.generate,
        segments,
        system=system,
        transient_exceptions=TRANSIENT_EXCEPTIONS,
        on_retry=on_retry,
    )

    return sanitize_mermaid_blocks(content)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/worker/test_page_draft.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/page_draft.py tests/worker/test_page_draft.py
git commit -m "feat: add page draft pass (Pass 2) with code-canonical prompt"
```

---

## Task 9: Fact-Check and Revision (Pass 3 + Pass 4)

**Files:**
- Create: `worker/pipeline/fact_check.py`
- Test: `tests/worker/test_fact_check.py`

- [ ] **Step 1: Write failing tests for fact-check output parsing and fallback**

```python
# tests/worker/test_fact_check.py
import pytest
from worker.pipeline.fact_check import (
    FactCheckIssue,
    FactCheckResult,
    parse_fact_check_result,
    strip_failed_claim,
    strip_failed_diagram,
)


def test_parse_pass_verdict():
    raw = {"verdict": "pass", "issues": []}
    result = parse_fact_check_result(raw)
    assert result.verdict == "pass"
    assert result.issues == []


def test_parse_fail_verdict_with_claim_issue():
    raw = {
        "verdict": "fail",
        "issues": [
            {
                "kind": "claim",
                "claim": "Uses IndexFlatL2",
                "section": "## Architecture",
                "reason": "Actually uses IndexFlatIP",
                "suggested_fix": "Change L2 to IP",
            }
        ],
    }
    result = parse_fact_check_result(raw)
    assert result.verdict == "fail"
    assert len(result.issues) == 1
    assert result.issues[0].kind == "claim"
    assert result.issues[0].claim == "Uses IndexFlatL2"


def test_parse_fail_verdict_with_diagram_issue():
    raw = {
        "verdict": "fail",
        "issues": [
            {
                "kind": "diagram",
                "diagram_index": 0,
                "section": "## Flow",
                "reason": "Wrong arrow direction",
                "suggested_fix": "Reverse the arrow",
            }
        ],
    }
    result = parse_fact_check_result(raw)
    assert result.issues[0].kind == "diagram"
    assert result.issues[0].diagram_index == 0


def test_strip_failed_claim_removes_sentence():
    draft = (
        "## Architecture\n\n"
        "The system uses IndexFlatL2 for similarity search. "
        "It stores vectors in a flat structure.\n"
    )
    result = strip_failed_claim(draft, "uses IndexFlatL2", "Wrong index type")
    assert "IndexFlatL2" not in result
    assert "<!-- removed:" in result
    assert "It stores vectors" in result


def test_strip_failed_claim_no_match_returns_draft():
    draft = "## Architecture\n\nNo matching text here.\n"
    result = strip_failed_claim(draft, "some nonexistent claim", "reason")
    assert result == draft


def test_strip_failed_diagram_removes_mermaid_block():
    draft = (
        "## Flow\n\n"
        "**Diagram: Data flow**\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "*Source: main.py:1-10*\n\n"
        "Some text after.\n"
    )
    result = strip_failed_diagram(draft, section="## Flow", diagram_index=0, reason="Wrong flow")
    assert "```mermaid" not in result
    assert "<!-- diagram removed:" in result
    assert "Some text after." in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/worker/test_fact_check.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement fact_check.py**

```python
# worker/pipeline/fact_check.py
"""Pass 3 (fact-check) and Pass 4 (targeted revision) of the multi-pass page generator.

The fact-check pass verifies key_claims from the outline against source code
and checks diagram relationships. The revision pass applies targeted fixes
to the draft when issues are found.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.page_outline import PageOutline
from worker.utils.mermaid import sanitize_mermaid_blocks
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

logger = logging.getLogger("worker.fact_check")

_FACT_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["claim", "diagram"]},
                    "claim": {"type": "string"},
                    "diagram_index": {"type": "integer"},
                    "section": {"type": "string"},
                    "reason": {"type": "string"},
                    "suggested_fix": {"type": "string"},
                },
                "required": ["kind", "section", "reason", "suggested_fix"],
            },
        },
    },
    "required": ["verdict", "issues"],
}


@dataclass
class FactCheckIssue:
    """A single issue found by the fact-check pass."""

    kind: str  # "claim" or "diagram"
    section: str
    reason: str
    suggested_fix: str
    claim: str | None = None
    diagram_index: int | None = None


@dataclass
class FactCheckResult:
    """The result of a fact-check pass."""

    verdict: str  # "pass" or "fail"
    issues: list[FactCheckIssue] = field(default_factory=list)


def parse_fact_check_result(raw: dict[str, Any]) -> FactCheckResult:
    """Parse raw LLM JSON output into a FactCheckResult."""
    verdict = raw.get("verdict", "pass")
    issues = []
    for issue_raw in raw.get("issues", []):
        issues.append(
            FactCheckIssue(
                kind=issue_raw.get("kind", "claim"),
                section=issue_raw.get("section", ""),
                reason=issue_raw.get("reason", ""),
                suggested_fix=issue_raw.get("suggested_fix", ""),
                claim=issue_raw.get("claim"),
                diagram_index=issue_raw.get("diagram_index"),
            )
        )
    return FactCheckResult(verdict=verdict, issues=issues)


_FACT_CHECK_SYSTEM = (
    "You are a technical accuracy reviewer. Your job is to verify claims "
    "and diagrams in a wiki page draft against the actual source code. "
    "You must be precise: only flag issues where the draft demonstrably "
    "contradicts or is unsupported by the source code provided.\n\n"
    "Output ONLY valid JSON."
)


def _build_fact_check_prompt(
    draft: str,
    outline: PageOutline,
    entity_summaries: str,
    dep_info: str | None,
    targeted_chunks: str,
) -> list[PromptSegment]:
    """Build the fact-check prompt with cacheable entity context."""
    # Cacheable prefix: entity summaries + dep info (same as outline pass)
    cached_parts = [f"Entity summaries:\n{entity_summaries}\n"]
    if dep_info:
        cached_parts.append(f"Dependencies:\n{dep_info}\n")

    # Variable tail: draft + claims + targeted chunks
    claims_json = json.dumps(outline.key_claims, indent=2)

    # Collect diagram info for verification
    diagrams = []
    for i, s in enumerate(outline.sections):
        if s.diagram:
            diagrams.append({
                "index": i,
                "section": s.heading,
                "type": s.diagram.type,
                "purpose": s.diagram.purpose,
                "source_files": s.diagram.source_files,
            })
    diagrams_json = json.dumps(diagrams, indent=2)

    schema_json = json.dumps(_FACT_CHECK_SCHEMA, indent=2)

    tail = (
        f"## Draft to verify:\n{draft}\n\n"
        f"## Key claims to verify:\n{claims_json}\n\n"
        f"## Diagrams to verify:\n{diagrams_json}\n\n"
        f"## Relevant source code:\n{targeted_chunks}\n\n"
        "For each claim, check if the source code supports it. "
        "For each diagram, check if the relationships depicted exist in the code. "
        "Only flag issues that are clearly wrong — do not flag stylistic concerns.\n\n"
        f"Output JSON matching this schema:\n{schema_json}"
    )

    return [
        PromptSegment(text="\n".join(cached_parts), cacheable=True),
        PromptSegment(text=tail),
    ]


async def run_fact_check(
    draft: str,
    outline: PageOutline,
    entity_summaries: str,
    dep_info: str | None,
    targeted_chunks: str,
    fast_llm: LLMProvider,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
) -> FactCheckResult:
    """Run fact-check on a draft using the fast model.

    Returns a FactCheckResult with verdict "pass" or "fail".
    On LLM/parsing errors, returns a "pass" verdict (fail-open)
    to avoid blocking page generation.
    """
    from worker.pipeline.language import get_language_instruction

    segments = _build_fact_check_prompt(
        draft, outline, entity_summaries, dep_info, targeted_chunks
    )
    system = _FACT_CHECK_SYSTEM + get_language_instruction(wiki_language)

    try:
        raw = await async_retry(
            fast_llm.generate_structured,
            segments,
            schema=_FACT_CHECK_SCHEMA,
            system=system,
            transient_exceptions=TRANSIENT_EXCEPTIONS,
            on_retry=on_retry,
        )
        return parse_fact_check_result(raw)
    except Exception:
        logger.warning("Fact-check LLM call failed, treating as pass", exc_info=True)
        return FactCheckResult(verdict="pass")


def strip_failed_claim(draft: str, claim: str, reason: str) -> str:
    """Remove a sentence containing the claim text from the draft.

    Uses case-insensitive substring matching. If the claim text is found,
    removes the containing sentence and inserts an HTML comment.
    If not found, returns the draft unchanged.
    """
    # Find sentences containing the claim text (case-insensitive)
    claim_lower = claim.lower()
    lines = draft.split("\n")
    result_lines = []
    for line in lines:
        if claim_lower in line.lower():
            # Split line into sentences and remove matching ones
            sentences = re.split(r'(?<=[.!?])\s+', line)
            kept = []
            removed = False
            for sentence in sentences:
                if claim_lower in sentence.lower():
                    removed = True
                else:
                    kept.append(sentence)
            if removed:
                replacement = " ".join(kept)
                if replacement:
                    result_lines.append(replacement)
                result_lines.append(f"<!-- removed: {reason} -->")
            else:
                result_lines.append(line)
        else:
            result_lines.append(line)
    return "\n".join(result_lines)


def strip_failed_diagram(
    draft: str, section: str, diagram_index: int, reason: str
) -> str:
    """Remove a specific mermaid block and its header/source from the draft.

    Locates mermaid blocks within the given section and removes the one
    at *diagram_index* (0-based within the section), including its
    preceding **Diagram: ...** header and following *Source: ...* line.
    """
    # Find the section boundary
    section_header = section.strip()
    section_start = draft.find(section_header)
    if section_start == -1:
        # Fallback: remove the nth mermaid block overall
        section_start = 0

    # Find the next section header (## ...) after this one
    next_section = re.search(
        r"^## ", draft[section_start + len(section_header):], re.MULTILINE
    )
    section_end = (
        section_start + len(section_header) + next_section.start()
        if next_section
        else len(draft)
    )

    section_text = draft[section_start:section_end]

    # Find all mermaid blocks in this section
    mermaid_pattern = re.compile(
        r'(\*\*Diagram:[^\n]*\*\*\s*\n\s*\n)?'
        r'(```mermaid\n.*?```)'
        r'(\s*\n\s*\*Source:[^\n]*\*)?',
        re.DOTALL,
    )
    matches = list(mermaid_pattern.finditer(section_text))

    if diagram_index < len(matches):
        match = matches[diagram_index]
        replacement = f"<!-- diagram removed: {reason} -->"
        new_section = (
            section_text[: match.start()]
            + replacement
            + section_text[match.end():]
        )
        draft = draft[:section_start] + new_section + draft[section_end:]

    return draft


_REVISION_SYSTEM = (
    "You are a technical writer revising a wiki page to fix specific factual "
    "errors. Revise ONLY the sections mentioned in the issues — leave "
    "everything else EXACTLY as-is, character for character.\n"
    "Output the complete revised Markdown page."
)

_DIAGRAM_REVISION_SYSTEM = (
    "You are a technical writer fixing a specific Mermaid diagram. "
    "Output ONLY the corrected ```mermaid block, nothing else."
)


async def run_targeted_revision(
    draft: str,
    issues: list[FactCheckIssue],
    context_segments: list[PromptSegment],
    llm: LLMProvider,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
) -> str:
    """Apply targeted revision to fix fact-check issues.

    Handles prose claim issues and diagram issues separately:
    - Claim issues: single LLM call to revise affected sections
    - Diagram issues: per-diagram LLM call to regenerate the mermaid block

    Returns the revised draft.
    """
    from worker.pipeline.language import get_language_instruction

    claim_issues = [i for i in issues if i.kind == "claim"]
    diagram_issues = [i for i in issues if i.kind == "diagram"]

    revised = draft

    # 1. Fix claim issues (single LLM call)
    if claim_issues:
        issues_json = json.dumps(
            [
                {
                    "claim": i.claim,
                    "section": i.section,
                    "reason": i.reason,
                    "suggested_fix": i.suggested_fix,
                }
                for i in claim_issues
            ],
            indent=2,
        )
        tail = (
            f"## Current draft:\n{revised}\n\n"
            f"## Issues to fix:\n{issues_json}\n\n"
            "Revise only the sections containing these issues. "
            "Leave every other section verbatim.\n"
            "Output the complete revised Markdown."
        )
        revision_segments = list(context_segments) + [PromptSegment(text=tail)]
        system = _REVISION_SYSTEM + get_language_instruction(wiki_language)

        revised = await async_retry(
            llm.generate,
            revision_segments,
            system=system,
            transient_exceptions=TRANSIENT_EXCEPTIONS,
            on_retry=on_retry,
        )
        revised = sanitize_mermaid_blocks(revised)

    # 2. Fix diagram issues (per-diagram LLM call)
    for diag_issue in diagram_issues:
        # Extract the mermaid block
        mermaid_pattern = re.compile(r'```mermaid\n(.*?)```', re.DOTALL)
        section_header = diag_issue.section.strip()
        section_start = revised.find(section_header)
        if section_start == -1:
            continue

        # Find diagrams after section header
        section_text = revised[section_start:]
        matches = list(mermaid_pattern.finditer(section_text))
        idx = diag_issue.diagram_index or 0
        if idx >= len(matches):
            continue

        original_block = matches[idx].group(0)
        tail = (
            f"Section: {section_header}\n"
            f"Current diagram:\n{original_block}\n\n"
            f"Issue: {diag_issue.reason}\n"
            f"Suggested fix: {diag_issue.suggested_fix}\n\n"
            "Output ONLY the corrected ```mermaid block."
        )
        diag_segments = list(context_segments) + [PromptSegment(text=tail)]
        system = _DIAGRAM_REVISION_SYSTEM + get_language_instruction(wiki_language)

        corrected = await async_retry(
            llm.generate,
            diag_segments,
            system=system,
            transient_exceptions=TRANSIENT_EXCEPTIONS,
            on_retry=on_retry,
        )
        corrected = sanitize_mermaid_blocks(corrected)

        # Ensure the corrected output is wrapped in mermaid fences
        if "```mermaid" not in corrected:
            corrected = f"```mermaid\n{corrected}\n```"

        # Splice corrected diagram back into the draft
        abs_start = section_start + matches[idx].start()
        abs_end = section_start + matches[idx].end()
        revised = revised[:abs_start] + corrected + revised[abs_end:]

    return revised
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/worker/test_fact_check.py -v`
Expected: All tests PASS

- [ ] **Step 5: Write integration test for the full fact-check → revision flow**

```python
# append to tests/worker/test_fact_check.py
from unittest.mock import AsyncMock
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.page_outline import DiagramPlan, PageOutline, SectionPlan


async def test_run_fact_check_returns_pass():
    from worker.pipeline.fact_check import run_fact_check

    mock_fast_llm = AsyncMock()
    mock_fast_llm.generate_structured.return_value = {
        "verdict": "pass",
        "issues": [],
    }

    outline = PageOutline(
        sections=[
            SectionPlan(heading="Overview", kind="prose", focus="f",
                        diagram=DiagramPlan(type="flowchart", purpose="p", source_files=["a.py"])),
        ],
        key_claims=["claim1", "claim2", "claim3"],
    )
    result = await run_fact_check(
        draft="## Overview\nContent here.",
        outline=outline,
        entity_summaries="entities",
        dep_info=None,
        targeted_chunks="code chunks",
        fast_llm=mock_fast_llm,
    )
    assert result.verdict == "pass"
    assert result.issues == []


async def test_run_fact_check_returns_fail():
    from worker.pipeline.fact_check import run_fact_check

    mock_fast_llm = AsyncMock()
    mock_fast_llm.generate_structured.return_value = {
        "verdict": "fail",
        "issues": [
            {
                "kind": "claim",
                "claim": "Wrong claim",
                "section": "## Overview",
                "reason": "Not supported by code",
                "suggested_fix": "Remove claim",
            }
        ],
    }

    outline = PageOutline(
        sections=[
            SectionPlan(heading="Overview", kind="prose", focus="f",
                        diagram=DiagramPlan(type="flowchart", purpose="p", source_files=["a.py"])),
        ],
        key_claims=["Wrong claim", "claim2", "claim3"],
    )
    result = await run_fact_check(
        draft="## Overview\nWrong claim here.",
        outline=outline,
        entity_summaries="entities",
        dep_info=None,
        targeted_chunks="code chunks",
        fast_llm=mock_fast_llm,
    )
    assert result.verdict == "fail"
    assert len(result.issues) == 1


async def test_run_fact_check_fails_open_on_error():
    from worker.pipeline.fact_check import run_fact_check

    mock_fast_llm = AsyncMock()
    mock_fast_llm.generate_structured.side_effect = RuntimeError("LLM error")

    outline = PageOutline(
        sections=[
            SectionPlan(heading="X", kind="prose", focus="f",
                        diagram=DiagramPlan(type="flowchart", purpose="p", source_files=[])),
        ],
        key_claims=["a", "b", "c"],
    )
    result = await run_fact_check(
        draft="content",
        outline=outline,
        entity_summaries="",
        dep_info=None,
        targeted_chunks="",
        fast_llm=mock_fast_llm,
    )
    assert result.verdict == "pass"


async def test_run_targeted_revision_fixes_claims():
    from worker.pipeline.fact_check import FactCheckIssue, run_targeted_revision

    mock_llm = AsyncMock()
    mock_llm.generate.return_value = "## Overview\n\nRevised content here."

    issues = [
        FactCheckIssue(
            kind="claim",
            claim="wrong claim",
            section="## Overview",
            reason="Incorrect",
            suggested_fix="Fix it",
        )
    ]
    context = [PromptSegment(text="context", cacheable=True)]

    result = await run_targeted_revision(
        draft="## Overview\n\nOriginal with wrong claim.",
        issues=issues,
        context_segments=context,
        llm=mock_llm,
    )
    assert "Revised content" in result
    mock_llm.generate.assert_called_once()
```

- [ ] **Step 6: Run all fact-check tests**

Run: `pytest tests/worker/test_fact_check.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add worker/pipeline/fact_check.py tests/worker/test_fact_check.py
git commit -m "feat: add fact-check (Pass 3) and targeted revision (Pass 4)"
```

---

## Task 10: Diagram Post-Processor

**Files:**
- Create: `worker/pipeline/diagram_post_processor.py`
- Test: `tests/worker/test_diagram_post_processor.py`

- [ ] **Step 1: Write failing tests for diagram header/source enforcement**

```python
# tests/worker/test_diagram_post_processor.py
from worker.pipeline.diagram_post_processor import ensure_diagram_headers


def test_compliant_block_untouched():
    md = (
        "## Section\n\n"
        "**Diagram: Data flow**\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "*Source: main.py:1-10*\n"
    )
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert result == md


def test_missing_header_inserted():
    md = (
        "## Section\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "*Source: main.py:1-10*\n"
    )
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert "**Diagram:" in result
    assert "```mermaid" in result


def test_missing_source_inserted():
    md = (
        "## Section\n\n"
        "**Diagram: Flow**\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n"
    )
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert "*Source:" in result


def test_missing_both_inserted():
    md = (
        "## Section\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n"
    )
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert "**Diagram:" in result
    assert "*Source:" in result


def test_multiple_blocks_handled():
    md = (
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "Some text.\n\n"
        "```mermaid\nsequenceDiagram\n  A->>B: call\n```\n"
    )
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert result.count("**Diagram:") == 2
    assert result.count("*Source:") == 2


def test_no_mermaid_blocks_returns_unchanged():
    md = "## Section\n\nJust text, no diagrams.\n"
    result = ensure_diagram_headers(md, default_source_files=["main.py"])
    assert result == md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/worker/test_diagram_post_processor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement diagram_post_processor.py**

```python
# worker/pipeline/diagram_post_processor.py
"""Post-processor that ensures every Mermaid diagram block has a header and source reference.

Checks each ```mermaid block for a preceding **Diagram: ...** header and a
following *Source: ...* annotation. Inserts placeholder values when missing.
"""

from __future__ import annotations

import re


_MERMAID_BLOCK = re.compile(r'```mermaid\n.*?```', re.DOTALL)
_HEADER_PATTERN = re.compile(r'\*\*Diagram:[^\n]*\*\*')
_SOURCE_PATTERN = re.compile(r'\*Source:[^\n]*\*')


def ensure_diagram_headers(
    markdown: str,
    default_source_files: list[str] | None = None,
) -> str:
    """Ensure every mermaid block has a header and source reference.

    Scans the Markdown for ```mermaid blocks. For each:
    - If no **Diagram: ...** header exists in the 3 lines preceding the block,
      inserts **Diagram: Diagram** before it.
    - If no *Source: ...* annotation exists in the 3 lines following the block,
      inserts *Source: <first default_source_file>* after it.

    Args:
        markdown: The full Markdown content to process.
        default_source_files: Fallback source files for the *Source:* annotation
            when the draft doesn't include one.

    Returns:
        The processed Markdown with headers and source annotations ensured.
    """
    if not markdown:
        return markdown

    default_source = (
        default_source_files[0] if default_source_files else "unknown"
    )

    # Process blocks from end to start so insertions don't shift offsets
    matches = list(_MERMAID_BLOCK.finditer(markdown))
    for match in reversed(matches):
        block_start = match.start()
        block_end = match.end()

        # Check for header in the 3 lines before the block
        prefix_start = max(0, block_start - 200)
        prefix = markdown[prefix_start:block_start]
        has_header = bool(_HEADER_PATTERN.search(prefix.split("\n")[-3:] and prefix))
        # More precise: look at lines immediately before
        lines_before = prefix.rstrip().split("\n")
        has_header = any(
            _HEADER_PATTERN.search(line)
            for line in lines_before[-3:]
        )

        # Check for source in the 3 lines after the block
        suffix_end = min(len(markdown), block_end + 200)
        suffix = markdown[block_end:suffix_end]
        suffix_lines = suffix.lstrip("\n").split("\n")[:3]
        has_source = any(
            _SOURCE_PATTERN.search(line)
            for line in suffix_lines
        )

        # Insert missing source after block
        if not has_source:
            source_line = f"\n\n*Source: {default_source}*"
            markdown = markdown[:block_end] + source_line + markdown[block_end:]

        # Insert missing header before block
        if not has_header:
            header_line = "**Diagram: Diagram**\n\n"
            markdown = markdown[:block_start] + header_line + markdown[block_start:]

    return markdown
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/worker/test_diagram_post_processor.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/diagram_post_processor.py tests/worker/test_diagram_post_processor.py
git commit -m "feat: add diagram header/source post-processor"
```

---

## Task 11: Multi-Pass Orchestrator (Replaces Single-Pass Generator)

**Files:**
- Modify: `worker/pipeline/page_generator.py`
- Modify: `tests/worker/test_page_generator.py`

This task replaces the single-pass `generate_page` and `generate_page_batch` with the multi-pass orchestrator that calls outline → draft → fact-check → revision.

- [ ] **Step 1: Write failing tests for the new multi-pass generate_page**

```python
# tests/worker/test_page_generator.py (rewrite)
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np

from worker.pipeline.page_generator import (
    PageResult,
    compute_generation_order,
    generate_page,
    generate_page_batch,
)
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan


def _make_store():
    from worker.pipeline.rag_indexer import FAISSStore
    tmpdir = tempfile.mkdtemp()
    store = FAISSStore(
        dimension=1536,
        index_path=Path(tmpdir) / "idx",
        meta_path=Path(tmpdir) / "meta.pkl",
    )
    store.add(
        [np.zeros(1536, dtype=np.float32)],
        [{"text": "class User: pass", "file": "models.py", "start_line": 1, "end_line": 1}],
    )
    return store


def _make_mock_llm():
    """Mock LLM that returns structured outline, then draft text."""
    m = AsyncMock()
    # generate_structured returns outline JSON (for the outline pass)
    m.generate_structured.return_value = {
        "sections": [
            {"heading": "Overview", "kind": "prose", "focus": "What it does", "diagram": None},
            {
                "heading": "Architecture",
                "kind": "prose+diagram",
                "focus": "How it works",
                "diagram": {
                    "type": "flowchart",
                    "purpose": "Component flow",
                    "source_files": ["models.py"],
                },
            },
        ],
        "key_claims": [
            "User class defines the data model",
            "Models are stored in SQLite",
            "User has an id field",
        ],
    }
    # generate returns draft text (for the draft pass)
    m.generate.return_value = (
        "## Overview\n\nThe models module defines data classes.\n\n"
        "## Architecture\n\n"
        "**Diagram: Component flow**\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "*Source: models.py:1-10*\n"
    )
    return m


def _make_mock_fast_llm():
    """Mock fast LLM for outline + fact-check passes."""
    m = AsyncMock()
    # Outline pass
    m.generate_structured.side_effect = [
        # First call: outline
        {
            "sections": [
                {"heading": "Overview", "kind": "prose", "focus": "What it does", "diagram": None},
                {
                    "heading": "Architecture",
                    "kind": "prose+diagram",
                    "focus": "How it works",
                    "diagram": {
                        "type": "flowchart",
                        "purpose": "Component flow",
                        "source_files": ["models.py"],
                    },
                },
            ],
            "key_claims": [
                "User class defines the data model",
                "Models are stored in SQLite",
                "User has an id field",
            ],
        },
        # Second call: fact-check
        {"verdict": "pass", "issues": []},
    ]
    return m


async def test_generate_page_multi_pass(mock_embedding):
    store = _make_store()
    llm = _make_mock_llm()
    fast_llm = _make_mock_fast_llm()

    spec = WikiPageSpec(title="Models", purpose="Data model classes.", files=["models.py"])
    result = await generate_page(
        spec, store, llm, fast_llm, mock_embedding, repo_name="test",
    )
    assert isinstance(result, PageResult)
    assert result.slug == "models"
    assert len(result.content) > 0
    # Verify both LLMs were called
    assert fast_llm.generate_structured.call_count == 2  # outline + fact-check
    assert llm.generate.call_count == 1  # draft only


async def test_generate_page_with_fact_check_fail_triggers_revision(mock_embedding):
    store = _make_store()
    llm = _make_mock_llm()
    fast_llm = AsyncMock()

    fast_llm.generate_structured.side_effect = [
        # Outline
        {
            "sections": [
                {"heading": "Overview", "kind": "prose", "focus": "f",
                 "diagram": {"type": "flowchart", "purpose": "p", "source_files": ["models.py"]}},
            ],
            "key_claims": ["claim 1", "claim 2", "claim 3"],
        },
        # Fact-check: fail
        {
            "verdict": "fail",
            "issues": [{
                "kind": "claim",
                "claim": "claim 1",
                "section": "## Overview",
                "reason": "Not supported",
                "suggested_fix": "Remove it",
            }],
        },
        # Second fact-check after revision: pass
        {"verdict": "pass", "issues": []},
    ]

    # First generate call: draft. Second: revision.
    llm.generate.side_effect = [
        "## Overview\n\n**Diagram: Flow**\n\n```mermaid\nflowchart TD\n  A-->B\n```\n\n*Source: models.py:1-10*",
        "## Overview\n\nRevised content.\n\n**Diagram: Flow**\n\n```mermaid\nflowchart TD\n  A-->B\n```\n\n*Source: models.py:1-10*",
    ]

    spec = WikiPageSpec(title="Models", purpose="Test.", files=["models.py"])
    result = await generate_page(
        spec, store, llm, fast_llm, mock_embedding, repo_name="test",
    )
    assert llm.generate.call_count == 2  # draft + revision


def test_compute_generation_order_unchanged():
    """Verify compute_generation_order still works (no changes to it)."""
    plan = WikiPlan(pages=[
        WikiPageSpec(title="Overview", purpose="Root."),
        WikiPageSpec(title="API", purpose="API.", parent="Overview"),
        WikiPageSpec(title="Worker", purpose="Worker.", parent="Overview"),
    ])
    levels = compute_generation_order(plan)
    assert len(levels) == 2
    # Deepest first
    assert all(p.parent == "Overview" for p in levels[0])
    assert levels[1][0].title == "Overview"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/worker/test_page_generator.py::test_generate_page_multi_pass -v`
Expected: FAIL — signature mismatch (no `fast_llm` parameter)

- [ ] **Step 3: Rewrite page_generator.py orchestrator**

Modify `worker/pipeline/page_generator.py`. Keep `compute_generation_order`, `PageResult`, `_format_entity_details`, `_format_context_chunks` (they are used by the new passes). Replace `_build_page_prompt`, `generate_page`, and `generate_page_batch` with the multi-pass orchestrator:

```python
# Replace generate_page with multi-pass version
async def generate_page(
    spec: WikiPageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    fast_llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    top_k: int = 12,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
    child_contents: list[PageResult] | None = None,
) -> PageResult:
    """Generate a wiki page using the multi-pass pipeline.

    Pass 1 (outline): fast_llm produces a structured outline.
    Pass 2 (draft): llm generates the full Markdown from the outline.
    Pass 3 (fact-check): fast_llm verifies key claims against source.
    Pass 4 (revision): llm fixes issues if fact-check fails (max 1 attempt).
    """
    from worker.pipeline.dependency_graph import summarize_page_deps
    from worker.pipeline.diagram_post_processor import ensure_diagram_headers
    from worker.pipeline.fact_check import (
        run_fact_check,
        run_targeted_revision,
        strip_failed_claim,
        strip_failed_diagram,
    )
    from worker.pipeline.page_draft import generate_draft, build_draft_prompt
    from worker.pipeline.page_outline import generate_page_outline

    # ── RAG retrieval (shared across passes) ──
    queries = [f"{spec.title} {' '.join((spec.files or [])[:5])}"]
    if spec.purpose:
        queries.append(spec.purpose)
    if entity_details:
        entity_names = [e.get("name", "") for e in entity_details[:5] if e.get("name")]
        if entity_names:
            queries.append(" ".join(entity_names))

    query_vecs = []
    for q in queries:
        vec = await async_retry(
            embedding.embed, q,
            transient_exceptions=TRANSIENT_EXCEPTIONS, on_retry=on_retry,
        )
        query_vecs.append(vec)

    if len(query_vecs) > 1:
        context_chunks = store.multi_search(query_vecs, k=top_k, doc_k=1)
    else:
        context_chunks = store.search(query_vecs[0], k=top_k, doc_k=1)

    # ── Build reusable context strings ──
    entity_summaries = _format_entity_details(entity_details or [])
    dep_info_str = None
    if dep_info:
        dep_lines = []
        for key in ("depends_on", "depended_by", "external_deps"):
            vals = dep_info.get(key, [])
            if vals:
                dep_lines.append(f"- {key}: {', '.join(vals[:10])}")
        dep_info_str = "\n".join(dep_lines) if dep_lines else None

    child_titles = [c.title for c in child_contents] if child_contents else None

    # ── Pass 1: Outline (fast model) ──
    outline = await generate_page_outline(
        spec=spec,
        entity_summaries=entity_summaries,
        dep_info=dep_info_str,
        fast_llm=fast_llm,
        on_retry=on_retry,
        child_titles=child_titles,
        wiki_language=wiki_language,
    )

    # ── Pass 2: Draft (main model) ──
    draft = await generate_draft(
        spec=spec,
        outline=outline,
        context_chunks=context_chunks,
        repo_name=repo_name,
        llm=llm,
        dep_info=dep_info,
        entity_details=entity_details,
        child_contents=child_contents,
        on_retry=on_retry,
        wiki_language=wiki_language,
    )

    # ── Pass 3: Fact-check (fast model) ──
    targeted_chunks = _format_context_chunks(context_chunks)
    fc_result = await run_fact_check(
        draft=draft,
        outline=outline,
        entity_summaries=entity_summaries,
        dep_info=dep_info_str,
        targeted_chunks=targeted_chunks,
        fast_llm=fast_llm,
        on_retry=on_retry,
        wiki_language=wiki_language,
    )

    # ── Pass 4: Targeted revision (main model, conditional) ──
    if fc_result.verdict == "fail" and fc_result.issues:
        # Build cacheable context segments for revision (reuses draft's cache)
        context_segments = build_draft_prompt(
            spec=spec, outline=outline, context_chunks=context_chunks,
            repo_name=repo_name, dep_info=dep_info,
            entity_details=entity_details, child_contents=child_contents,
        )
        # Keep only the cacheable prefix segments
        cache_segs = [s for s in context_segments if s.cacheable]

        draft = await run_targeted_revision(
            draft=draft,
            issues=fc_result.issues,
            context_segments=cache_segs,
            llm=llm,
            on_retry=on_retry,
            wiki_language=wiki_language,
        )

        # If still failing after revision, apply deterministic fallback
        # (We don't re-run fact-check; one revision attempt max)
        # Strip remaining claims/diagrams that were flagged
        for issue in fc_result.issues:
            if issue.kind == "claim" and issue.claim:
                draft = strip_failed_claim(draft, issue.claim, issue.reason)
            elif issue.kind == "diagram" and issue.diagram_index is not None:
                draft = strip_failed_diagram(
                    draft, issue.section, issue.diagram_index, issue.reason
                )

    # ── Post-processing ──
    draft = ensure_diagram_headers(draft, default_source_files=spec.files)
    draft = sanitize_mermaid_blocks(draft)

    return PageResult(slug=spec.slug, title=spec.title, content=draft)
```

Replace `generate_page_batch` similarly — it calls the new `generate_page` for each spec:

```python
async def generate_page_batch(
    specs_with_children: list[tuple[WikiPageSpec, list[PageResult] | None]],
    store: FAISSStore,
    llm: LLMProvider,
    fast_llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    file_analysis: FileAnalysis,
    dep_graph: DependencyGraph,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
) -> list[PageResult]:
    """Generate all pages in a batch using the multi-pass pipeline."""
    from worker.pipeline.dependency_graph import summarize_page_deps

    async def _gen_one(
        spec: WikiPageSpec, children: list[PageResult] | None
    ) -> PageResult:
        entities = []
        for rel_path in spec.files or []:
            file_info = file_analysis.files.get(rel_path)
            if file_info:
                for e in file_info.entities:
                    entities.append({**e, "file": rel_path})

        dep_info = summarize_page_deps(spec.files or [], dep_graph)
        dep_info_or_none = dep_info if any(dep_info.values()) else None
        entities_or_none = entities if entities else None

        return await generate_page(
            spec=spec,
            store=store,
            llm=llm,
            fast_llm=fast_llm,
            embedding=embedding,
            repo_name=repo_name,
            dep_info=dep_info_or_none,
            entity_details=entities_or_none,
            on_retry=on_retry,
            wiki_language=wiki_language,
            child_contents=children,
        )

    # Use semaphore for concurrency control (matching previous generate_batch behavior)
    import asyncio
    sem = asyncio.Semaphore(5)

    async def _bounded(spec, children):
        async with sem:
            return await _gen_one(spec, children)

    results = await asyncio.gather(
        *[_bounded(spec, children) for spec, children in specs_with_children]
    )
    return list(results)
```

Remove the old `_build_page_prompt`, `_SYSTEM`, `_PARENT_TEMPLATE` constants, and old `generate_page`/`generate_page_batch` implementations. Keep `compute_generation_order`, `PageResult`, `_format_entity_details`, `_format_context_chunks`.

- [ ] **Step 4: Run page generator tests**

Run: `pytest tests/worker/test_page_generator.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/page_generator.py tests/worker/test_page_generator.py
git commit -m "feat: replace single-pass generator with multi-pass orchestrator"
```

---

## Task 12: Thread fast_llm Through jobs.py and Planner

**Files:**
- Modify: `worker/jobs.py`
- Modify: `worker/pipeline/wiki_planner.py`
- Modify: `tests/conftest.py`
- Test: `tests/worker/test_wiki_planner.py` (extend)

- [ ] **Step 1: Add mock_fast_llm fixture to conftest.py**

```python
# append to tests/conftest.py

@pytest.fixture
def mock_fast_llm():
    """Returns a mock fast LLMProvider for outline/fact-check passes."""
    m = AsyncMock()
    # Default: outline pass returns a simple plan
    m.generate_structured.return_value = {
        "sections": [
            {"heading": "Overview", "kind": "prose", "focus": "Overview", "diagram": None},
            {
                "heading": "Components",
                "kind": "prose+diagram",
                "focus": "Main components",
                "diagram": {
                    "type": "flowchart",
                    "purpose": "Component relationships",
                    "source_files": ["main.py"],
                },
            },
        ],
        "key_claims": [
            "Main function is the entry point",
            "Config loaded from environment",
            "Server binds to port 3000",
        ],
    }
    m.generate.return_value = "Mocked fast model response."
    m.generate_batch.side_effect = lambda prompts, **kw: [
        "Mocked fast response." for _ in prompts
    ]
    return m
```

- [ ] **Step 2: Write failing test for planner Phase 2 using fast_llm**

```python
# append to tests/worker/test_wiki_planner.py
async def test_planner_phase2_uses_fast_llm(mock_llm, mock_fast_llm):
    """Phase 2 (file assignment) should use fast_llm, not the main llm."""
    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
    from worker.pipeline.wiki_planner import generate_wiki_plan

    file_analysis = FileAnalysis(files={
        "main.py": FileInfo(entities=[], lines=10, imports=[]),
        "utils.py": FileInfo(entities=[], lines=5, imports=[]),
    })

    # Configure mock_llm for Phase 1 (outline)
    mock_llm.generate_structured.return_value = {
        "pages": [
            {"title": "Overview", "purpose": "Project overview."},
            {"title": "Utils", "purpose": "Utility functions."},
        ]
    }

    # Configure mock_fast_llm for Phase 2 (assignment)
    mock_fast_llm.generate_structured.return_value = {
        "assignments": [
            {"file": "main.py", "page_title": "Overview"},
            {"file": "utils.py", "page_title": "Utils"},
        ]
    }

    plan = await generate_wiki_plan(
        file_analysis,
        repo_name="test",
        llm=mock_llm,
        fast_llm=mock_fast_llm,
    )

    # Phase 1 used main llm
    assert mock_llm.generate_structured.call_count == 1
    # Phase 2 used fast llm
    assert mock_fast_llm.generate_structured.call_count == 1
    assert len(plan.pages) == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/worker/test_wiki_planner.py::test_planner_phase2_uses_fast_llm -v`
Expected: FAIL — `generate_wiki_plan` doesn't accept `fast_llm`

- [ ] **Step 4: Update wiki_planner.py to accept fast_llm for Phase 2**

Modify `worker/pipeline/wiki_planner.py`:

In `generate_wiki_plan()`, add `fast_llm: LLMProvider | None = None` parameter. Pass `fast_llm or llm` to `_assign_files()`:

```python
async def generate_wiki_plan(
    file_analysis: FileAnalysis,
    repo_name: str,
    llm: LLMProvider,
    dep_graph: DependencyGraph | None = None,
    max_retries: int = 3,
    readme: str | None = None,
    on_retry: OnRetryCallback | None = None,
    existing_titles: set[str] | None = None,
    wiki_language: str = "en",
    fast_llm: LLMProvider | None = None,
) -> WikiPlan:
    # ... existing Phase 1 code using llm ...

    # Phase 2: Assign files + validate assignments (use fast model)
    phase2_llm = fast_llm or llm
    file_assignments = await _assign_files(
        outline=outline,
        file_summary=file_summary,
        dep_info=dep_info,
        all_files=all_files,
        llm=phase2_llm,
        system=system,
        on_retry=on_retry,
        max_retries=max_retries,
    )
    # ... rest unchanged ...
```

- [ ] **Step 5: Update jobs.py to construct and pass fast_llm**

Modify `worker/jobs.py`:

At the top of `run_full_index` and `run_refresh_index`, after `llm = make_llm_provider(cfg)`, add:

```python
from worker.llm import make_fast_llm_provider
fast_llm = make_fast_llm_provider(cfg, llm)
```

Pass `fast_llm` to `generate_wiki_plan`:
```python
plan = await generate_wiki_plan(
    file_analysis,
    repo_name=name,
    llm=llm,
    fast_llm=fast_llm,
    dep_graph=dep_graph,
    readme=readme,
    on_retry=_on_retry,
    wiki_language=wiki_language,
)
```

Pass `fast_llm` to `generate_page_batch`:
```python
results = await generate_page_batch(
    specs_with_children,
    store,
    llm,
    fast_llm,
    embedding,
    repo_name=name,
    file_analysis=file_analysis,
    dep_graph=dep_graph,
    on_retry=_on_retry,
    wiki_language=wiki_language,
)
```

- [ ] **Step 6: Run wiki planner tests**

Run: `pytest tests/worker/test_wiki_planner.py -v`
Expected: All tests PASS

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ --ignore=tests/e2e -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add worker/jobs.py worker/pipeline/wiki_planner.py tests/conftest.py tests/worker/test_wiki_planner.py
git commit -m "feat: thread fast_llm through jobs and planner Phase 2"
```

---

## Task 12b (Follow-up): Convert Planner Prompts to PromptSegment

**Note:** Spec §7.2 calls for the planner's Phase 1 and Phase 2 prompts to use `PromptSegment` for intra-phase retry cache reuse. This is a cost optimization (not a functional change) and can be done as a follow-up after the core pipeline is working. The `_build_outline_prompt` and `_build_assignment_prompt` functions in `wiki_planner.py` would return `list[PromptSegment]` instead of `str`, marking the file-summary and dep-info sections as cacheable. This benefits large repos where Phase 1 self-retries 2–3 times with the same large context.

---

## Task 13: Update Existing Tests for New Signatures

**Files:**
- Modify: `tests/worker/test_page_generator.py`
- Modify: `tests/worker/test_jobs.py`

- [ ] **Step 1: Run full test suite to identify failures**

Run: `pytest tests/ --ignore=tests/e2e -v 2>&1 | head -100`
Expected: Some tests may fail due to changed signatures (e.g., `generate_page` now requires `fast_llm`)

- [ ] **Step 2: Fix any test_page_generator.py failures**

Update any remaining tests that call `generate_page` with the old 5-arg signature to include `fast_llm`. Use the `mock_fast_llm` fixture or create inline mocks.

- [ ] **Step 3: Fix any test_jobs.py failures**

Update `test_jobs.py` to mock `make_fast_llm_provider` or ensure the patched `make_llm_provider` returns a mock that also serves as `fast_llm`.

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ --ignore=tests/e2e -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "fix: update existing tests for multi-pass generator signatures"
```

---

## Task 14: Lint and Format

- [ ] **Step 1: Run ruff check and fix**

Run: `uv run ruff check . --fix`
Expected: No errors (or auto-fixed)

- [ ] **Step 2: Run ruff format**

Run: `uv run ruff format .`
Expected: Files reformatted

- [ ] **Step 3: Run npm lint**

Run: `npm run lint --prefix web`
Expected: No errors (frontend unchanged)

- [ ] **Step 4: Run full test suite one final time**

Run: `pytest tests/ --ignore=tests/e2e -v`
Expected: All tests PASS

- [ ] **Step 5: Commit lint fixes if any**

```bash
git add -u
git commit -m "style: lint and format fixes"
```

---

## Task 15: Integration Smoke Test

- [ ] **Step 1: Run end-to-end test against fixture repo**

Run: `pytest tests/worker/test_jobs.py -v -k "full_index"`
Expected: PASS — the full pipeline works with mocked LLMs

- [ ] **Step 2: Verify coverage target**

Run: `pytest tests/ --ignore=tests/e2e --cov=worker --cov-report=term-missing | tail -30`
Expected: ≥80% on `worker/`, ≥85% on new modules

- [ ] **Step 3: Final commit if needed**

```bash
git status  # verify clean tree
```
