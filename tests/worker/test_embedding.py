from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from worker.embedding.openai_embed import OpenAIEmbedding


def _openai_response(count: int, dim: int = 1536):
    return AsyncMock(data=[AsyncMock(embedding=[0.0] * dim) for _ in range(count)])


def _gemini_response(count: int, dim: int = 768):
    return MagicMock(embeddings=[MagicMock(values=[0.0] * dim) for _ in range(count)])


def _awaited_input_sizes(mock) -> list[int]:
    return [len(call.kwargs["input"]) for call in mock.await_args_list]


@pytest.fixture(autouse=True)
def mock_openai_client():
    with patch("worker.embedding.openai_embed.AsyncOpenAI") as mock:
        yield mock


async def test_embed_returns_float32_array():
    provider = OpenAIEmbedding(api_key="test-key")
    fake_vector = [0.1] * 1536
    # provider._client is already the mock from our fixture
    provider._client.embeddings.create = AsyncMock(
        return_value=AsyncMock(data=[AsyncMock(embedding=fake_vector)])
    )
    result = await provider.embed("hello world")
    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32
    assert result.shape == (1536,)


async def test_embed_batch_returns_list():
    provider = OpenAIEmbedding(api_key="test-key")
    provider._client.embeddings.create = AsyncMock(return_value=_openai_response(2))
    result = await provider.embed_batch(["a", "b"])
    assert len(result) == 2
    assert all(isinstance(v, np.ndarray) for v in result)


async def test_embed_batch_empty_returns_empty():
    provider = OpenAIEmbedding(api_key="test-key")
    result = await provider.embed_batch([])
    assert result == []


async def test_openai_embed_batch_uses_50_item_sub_batches():
    provider = OpenAIEmbedding(api_key="test-key")
    provider._client.embeddings.create = AsyncMock(
        side_effect=[_openai_response(50), _openai_response(1)]
    )

    result = await provider.embed_batch(["text"] * 51)

    assert len(result) == 51
    assert _awaited_input_sizes(provider._client.embeddings.create) == [50, 1]


async def test_openai_embed_batch_retries_only_failed_sub_batch():
    provider = OpenAIEmbedding(api_key="test-key")
    provider._client.embeddings.create = AsyncMock(
        side_effect=[
            _openai_response(50),
            TimeoutError("quota"),
            _openai_response(1),
        ]
    )
    with patch("worker.utils.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await provider.embed_batch(["text"] * 51)

    assert len(result) == 51
    assert _awaited_input_sizes(provider._client.embeddings.create) == [50, 1, 1]


async def test_gemini_embed_batch_converts_429_client_error_to_timeout():
    from worker.embedding import gemini_embed

    class FakeClientError(Exception):
        code = 429

    fake_client = MagicMock()
    fake_client.models.embed_content.side_effect = FakeClientError("quota")

    with (
        patch.object(gemini_embed, "_GENAI_AVAILABLE", True),
        patch.object(
            gemini_embed,
            "_genai_errors",
            MagicMock(ClientError=FakeClientError),
            create=True,
        ),
        patch.object(gemini_embed.genai, "Client", return_value=fake_client),
        patch("worker.utils.retry.asyncio.sleep", new_callable=AsyncMock),
    ):
        provider = gemini_embed.GeminiEmbedding(api_key="test-key")
        with pytest.raises(TimeoutError):
            await provider.embed_batch(["hello"])


async def test_gemini_embed_batch_batches_large_inputs():
    from worker.embedding import gemini_embed

    fake_client = MagicMock()
    fake_client.models.embed_content.side_effect = [
        _gemini_response(50),
        _gemini_response(1),
    ]

    with (
        patch.object(gemini_embed, "_GENAI_AVAILABLE", True),
        patch.object(gemini_embed.genai, "Client", return_value=fake_client),
    ):
        provider = gemini_embed.GeminiEmbedding(api_key="test-key")
        result = await provider.embed_batch(["text"] * 51)

    assert len(result) == 51
    assert fake_client.models.embed_content.call_count == 2


async def test_gemini_embed_batch_retries_only_failed_sub_batch():
    from worker.embedding import gemini_embed

    fake_client = MagicMock()
    fake_client.models.embed_content.side_effect = [
        _gemini_response(50),
        TimeoutError("quota"),
        _gemini_response(1),
    ]

    with (
        patch.object(gemini_embed, "_GENAI_AVAILABLE", True),
        patch.object(gemini_embed.genai, "Client", return_value=fake_client),
        patch("worker.utils.retry.asyncio.sleep", new_callable=AsyncMock),
    ):
        provider = gemini_embed.GeminiEmbedding(api_key="test-key")
        result = await provider.embed_batch(["text"] * 51)

    assert len(result) == 51
    assert fake_client.models.embed_content.call_count == 3


async def test_ollama_embed_batch_retries_only_failed_item():
    from worker.embedding.ollama_embed import OllamaEmbedding

    provider = OllamaEmbedding()
    provider.embed = AsyncMock(
        side_effect=[
            np.array([1.0], dtype=np.float32),
            TimeoutError("local transient"),
            np.array([2.0], dtype=np.float32),
        ]
    )
    with patch("worker.utils.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await provider.embed_batch(["a", "b"])

    assert [v.tolist() for v in result] == [[1.0], [2.0]]
    assert provider.embed.await_count == 3
    assert provider.embed.await_args_list[0].args == ("a",)
    assert provider.embed.await_args_list[1].args == ("b",)
    assert provider.embed.await_args_list[2].args == ("b",)


def test_make_embedding_provider_openai():
    from worker.embedding import make_embedding_provider

    cfg = MagicMock()
    cfg.embedding.provider = "openai"
    cfg.embedding.api_key = "test-key"
    cfg.embedding.model = "text-embedding-3-small"
    provider = make_embedding_provider(cfg)
    assert isinstance(provider, OpenAIEmbedding)


def test_make_embedding_provider_unknown_raises():
    from worker.embedding import make_embedding_provider

    cfg = MagicMock()
    cfg.embedding.provider = "unknown"
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        make_embedding_provider(cfg)
