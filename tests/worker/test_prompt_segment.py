from worker.llm.prompt_segment import PromptSegment, normalize_prompt, segments_to_text


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


def test_segments_to_text():
    segments = [
        PromptSegment(text="System: ", cacheable=True),
        PromptSegment(text="Hello world"),
    ]
    assert segments_to_text(segments) == "System: Hello world"


def test_segments_to_text_empty():
    assert segments_to_text([]) == ""


async def test_logging_provider_forwards_segment_list():
    from unittest.mock import AsyncMock

    from worker.llm.base import LoggingLLMProvider

    inner = AsyncMock()
    inner.generate.return_value = "response"
    provider = LoggingLLMProvider(inner)
    segments = [PromptSegment(text="cached", cacheable=True)]
    result = await provider.generate(segments, system="sys")
    inner.generate.assert_called_once_with(segments, system="sys")
    assert result == "response"


async def test_anthropic_provider_builds_cache_control_blocks(monkeypatch):
    from unittest.mock import MagicMock

    from worker.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="test-model")
    captured_kwargs = {}

    async def mock_create(**kwargs):
        captured_kwargs.update(kwargs)
        resp = MagicMock()
        resp.content = [MagicMock(text="response")]
        return resp

    monkeypatch.setattr(provider._client.messages, "create", mock_create)
    segments = [
        PromptSegment(text="cached context", cacheable=True),
        PromptSegment(text="variable tail"),
    ]
    await provider.generate(segments, system="sys")
    messages = captured_kwargs["messages"]
    content = messages[0]["content"]
    assert isinstance(content, list) and len(content) == 2
    assert content[0]["text"] == "cached context"
    assert content[0].get("cache_control") == {"type": "ephemeral"}
    assert content[1]["text"] == "variable tail"
    assert "cache_control" not in content[1]


async def test_anthropic_provider_plain_string_unchanged(monkeypatch):
    from unittest.mock import MagicMock

    from worker.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="test-model")
    captured_kwargs = {}

    async def mock_create(**kwargs):
        captured_kwargs.update(kwargs)
        resp = MagicMock()
        resp.content = [MagicMock(text="response")]
        return resp

    monkeypatch.setattr(provider._client.messages, "create", mock_create)
    await provider.generate("plain prompt", system="sys")
    messages = captured_kwargs["messages"]
    assert messages == [{"role": "user", "content": "plain prompt"}]


async def test_anthropic_provider_system_segments(monkeypatch):
    from unittest.mock import MagicMock

    from worker.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test-key", model="test-model")
    captured_kwargs = {}

    async def mock_create(**kwargs):
        captured_kwargs.update(kwargs)
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
    cacheable_blocks = [b for b in system if "cache_control" in b]
    assert len(cacheable_blocks) == 1
    assert cacheable_blocks[0]["cache_control"] == {"type": "ephemeral"}


async def test_logging_provider_forwards_string():
    from unittest.mock import AsyncMock

    from worker.llm.base import LoggingLLMProvider

    inner = AsyncMock()
    inner.generate.return_value = "response"
    provider = LoggingLLMProvider(inner)
    result = await provider.generate("plain prompt", system="sys")
    inner.generate.assert_called_once_with("plain prompt", system="sys")
    assert result == "response"


def test_anthropic_segments_to_content_max_breakpoints():
    from worker.llm.anthropic_provider import _segments_to_anthropic_content

    # 5 alternating cacheable runs separated by non-cacheable segments
    segments = []
    for i in range(5):
        segments.append(PromptSegment(text=f"cached-{i}", cacheable=True))
        if i < 4:
            segments.append(PromptSegment(text=f"gap-{i}", cacheable=False))

    result = _segments_to_anthropic_content(segments)
    assert isinstance(result, list)
    # Count blocks with cache_control
    cached_blocks = [b for b in result if "cache_control" in b]
    assert len(cached_blocks) == 4  # Only first 4 runs get cache_control


def test_anthropic_segments_to_content_all_cacheable():
    from worker.llm.anthropic_provider import _segments_to_anthropic_content

    segments = [
        PromptSegment(text="first", cacheable=True),
        PromptSegment(text="second", cacheable=True),
        PromptSegment(text="third", cacheable=True),
    ]
    result = _segments_to_anthropic_content(segments)
    assert isinstance(result, list)
    assert len(result) == 3
    # Only last gets cache_control
    assert "cache_control" not in result[0]
    assert "cache_control" not in result[1]
    assert "cache_control" in result[2]
    assert result[2]["cache_control"]["type"] == "ephemeral"
