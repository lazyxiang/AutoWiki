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

    monkeypatch.setattr(provider._client.chat.completions, "create", mock_create)

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

    from unittest.mock import AsyncMock, MagicMock

    import httpx

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
    from worker.llm import make_fast_llm_provider, make_llm_provider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config()
    main = make_llm_provider(cfg)
    fast = make_fast_llm_provider(cfg, main)
    assert fast is main


def test_make_fast_llm_provider_returns_different_when_fast_model_set(monkeypatch):
    """When fast_model is set, make_fast_llm_provider creates a new provider."""
    from shared.config import Config, LLMConfig
    from worker.llm import make_fast_llm_provider, make_llm_provider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = Config(
        llm=LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            fast_model="claude-haiku-4-5",
            api_key="test-key",
        )
    )
    main = make_llm_provider(cfg)
    fast = make_fast_llm_provider(cfg, main)
    assert fast is not main
