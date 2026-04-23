from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from worker.embedding.openai_embed import OpenAIEmbedding


async def test_embed_returns_float32_array():
    provider = OpenAIEmbedding(api_key="test-key")
    fake_vector = [0.1] * 1536
    with patch.object(
        provider._client.embeddings, "create", new_callable=AsyncMock
    ) as mock:
        mock.return_value = AsyncMock(data=[AsyncMock(embedding=fake_vector)])
        result = await provider.embed("hello world")
    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32
    assert result.shape == (1536,)


async def test_embed_batch_returns_list():
    provider = OpenAIEmbedding(api_key="test-key")
    fake_vector = [0.0] * 1536
    with patch.object(
        provider._client.embeddings, "create", new_callable=AsyncMock
    ) as mock:
        mock.return_value = AsyncMock(
            data=[AsyncMock(embedding=fake_vector), AsyncMock(embedding=fake_vector)]
        )
        result = await provider.embed_batch(["a", "b"])
    assert len(result) == 2
    assert all(isinstance(v, np.ndarray) for v in result)


async def test_embed_batch_empty_returns_empty():
    provider = OpenAIEmbedding(api_key="test-key")
    result = await provider.embed_batch([])
    assert result == []


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
    ):
        provider = gemini_embed.GeminiEmbedding(api_key="test-key")
        with pytest.raises(TimeoutError):
            await provider.embed_batch(["hello"])


async def test_gemini_embed_batch_uses_50_item_sub_batches():
    from worker.embedding import gemini_embed

    fake_client = MagicMock()
    fake_client.models.embed_content.side_effect = [
        MagicMock(embeddings=[MagicMock(values=[0.0] * 768) for _ in range(50)]),
        MagicMock(embeddings=[MagicMock(values=[0.0] * 768)]),
    ]

    with (
        patch.object(gemini_embed, "_GENAI_AVAILABLE", True),
        patch.object(gemini_embed.genai, "Client", return_value=fake_client),
    ):
        provider = gemini_embed.GeminiEmbedding(api_key="test-key")
        result = await provider.embed_batch(["text"] * 51)

    assert len(result) == 51
    assert fake_client.models.embed_content.call_count == 2
    assert (
        len(fake_client.models.embed_content.call_args_list[0].kwargs["contents"]) == 50
    )
    assert (
        len(fake_client.models.embed_content.call_args_list[1].kwargs["contents"]) == 1
    )


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
