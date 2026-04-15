from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker.llm.anthropic_provider import AnthropicProvider
from worker.llm.base import LLMProvider


def test_provider_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()


async def test_anthropic_generate_calls_api():
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
    with patch.object(
        provider._client.messages, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = AsyncMock(content=[AsyncMock(text="Hello")])
        result = await provider.generate("Say hello")
    assert result == "Hello"


async def test_anthropic_generate_structured_returns_dict():
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
    raw = (
        '{"pages": [{"title": "Overview", "purpose": "Overview of project.",'
        ' "primary_files": ["main.py"]}]}'
    )
    with patch.object(
        provider._client.messages, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = AsyncMock(content=[AsyncMock(text=raw)])
        result = await provider.generate_structured(
            "Make a plan", schema={"type": "object"}
        )
    assert result["pages"][0]["title"] == "Overview"


async def test_anthropic_generate_with_system():
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
    with patch.object(
        provider._client.messages, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = AsyncMock(content=[AsyncMock(text="result")])
        result = await provider.generate("prompt", system="You are helpful")
    assert result == "result"
    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["system"] == "You are helpful"


async def test_anthropic_generate_structured_strips_json_fence():
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
    raw_with_fence = '```json\n{"key": "value"}\n```'
    with patch.object(
        provider._client.messages, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = AsyncMock(content=[AsyncMock(text=raw_with_fence)])
        result = await provider.generate_structured("prompt", schema={})
    assert result == {"key": "value"}


def test_make_llm_provider_anthropic():
    from worker.llm import make_llm_provider
    from worker.llm.anthropic_provider import AnthropicProvider

    cfg = MagicMock()
    cfg.llm.provider = "anthropic"
    cfg.llm.api_key = "test-key"
    cfg.llm.model = "claude-sonnet-4-6"
    cfg.debug = False
    with patch.dict("os.environ", {"AUTOWIKI_DEBUG": "false"}):
        provider = make_llm_provider(cfg)
    assert isinstance(provider, AnthropicProvider)


def test_make_llm_provider_unknown_raises():
    from worker.llm import make_llm_provider

    cfg = MagicMock()
    cfg.llm.provider = "unknown"
    cfg.llm.api_key = ""
    cfg.debug = False
    with patch.dict("os.environ", {"AUTOWIKI_DEBUG": "false"}):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            make_llm_provider(cfg)


async def test_openai_provider_generate():
    from worker.llm.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="test-key")
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="OpenAI response"))]
    with patch.object(
        provider._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response
        result = await provider.generate("prompt")
    assert result == "OpenAI response"


async def test_generate_batch_default_impl():
    """Default generate_batch calls generate() for each prompt concurrently."""

    class FakeLLM(LLMProvider):
        async def generate(self, prompt: str, system: str = "") -> str:
            return f"response:{prompt}"

        async def generate_structured(self, prompt, schema, system=""):
            return {}

        async def generate_stream(self, prompt, system=""):
            yield ""

    llm = FakeLLM()
    results = await llm.generate_batch(["a", "b", "c"], system="sys")
    assert results == ["response:a", "response:b", "response:c"]


async def test_generate_batch_respects_max_concurrency():
    """At most max_concurrency calls run in parallel."""
    import asyncio

    concurrent = 0
    max_seen = 0

    class TrackingLLM(LLMProvider):
        async def generate(self, prompt: str, system: str = "") -> str:
            nonlocal concurrent, max_seen
            concurrent += 1
            max_seen = max(max_seen, concurrent)
            await asyncio.sleep(0.01)
            concurrent -= 1
            return prompt

        async def generate_structured(self, prompt, schema, system=""):
            return {}

        async def generate_stream(self, prompt, system=""):
            yield ""

    llm = TrackingLLM()
    await llm.generate_batch([f"p{i}" for i in range(10)], max_concurrency=3)
    assert max_seen <= 3


async def test_logging_provider_wraps_generate_batch():
    """LoggingLLMProvider delegates generate_batch to inner provider."""
    from worker.llm.base import LoggingLLMProvider

    class FakeLLM(LLMProvider):
        async def generate(self, prompt: str, system: str = "") -> str:
            return f"r:{prompt}"

        async def generate_structured(self, prompt, schema, system=""):
            return {}

        async def generate_stream(self, prompt, system=""):
            yield ""

    inner = FakeLLM()
    logged = LoggingLLMProvider(inner)
    results = await logged.generate_batch(["x", "y"])
    assert results == ["r:x", "r:y"]
