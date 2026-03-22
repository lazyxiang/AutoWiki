import pytest
from unittest.mock import AsyncMock, patch
from worker.llm.base import LLMProvider
from worker.llm.anthropic_provider import AnthropicProvider

def test_provider_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()

async def test_anthropic_generate_calls_api():
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
    with patch.object(provider._client.messages, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = AsyncMock(content=[AsyncMock(text="Hello")])
        result = await provider.generate("Say hello")
    assert result == "Hello"

async def test_anthropic_generate_structured_returns_dict():
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
    raw = '{"pages": [{"title": "Overview", "slug": "overview", "modules": ["."]}]}'
    with patch.object(provider._client.messages, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = AsyncMock(content=[AsyncMock(text=raw)])
        result = await provider.generate_structured("Make a plan", schema={"type": "object"})
    assert result["pages"][0]["slug"] == "overview"
