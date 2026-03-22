import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
from worker.embedding.openai_embed import OpenAIEmbedding


async def test_embed_returns_float32_array():
    provider = OpenAIEmbedding(api_key="test-key")
    fake_vector = [0.1] * 1536
    with patch.object(provider._client.embeddings, "create", new_callable=AsyncMock) as mock:
        mock.return_value = AsyncMock(data=[AsyncMock(embedding=fake_vector)])
        result = await provider.embed("hello world")
    assert isinstance(result, np.ndarray)
    assert result.dtype == np.float32
    assert result.shape == (1536,)


async def test_embed_batch_returns_list():
    provider = OpenAIEmbedding(api_key="test-key")
    fake_vector = [0.0] * 1536
    with patch.object(provider._client.embeddings, "create", new_callable=AsyncMock) as mock:
        mock.return_value = AsyncMock(data=[AsyncMock(embedding=fake_vector), AsyncMock(embedding=fake_vector)])
        result = await provider.embed_batch(["a", "b"])
    assert len(result) == 2
    assert all(isinstance(v, np.ndarray) for v in result)


async def test_embed_batch_empty_returns_empty():
    provider = OpenAIEmbedding(api_key="test-key")
    result = await provider.embed_batch([])
    assert result == []


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
