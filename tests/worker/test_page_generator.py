import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from worker.pipeline.page_generator import generate_page, PageResult
from worker.pipeline.wiki_planner import PageSpec

async def test_generate_page_returns_markdown(mock_llm, mock_embedding):
    # Set up a real FAISSStore with mock data
    import numpy as np
    from worker.pipeline.rag_indexer import FAISSStore
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        store = FAISSStore(dimension=1536,
                           index_path=Path(tmp) / "idx",
                           meta_path=Path(tmp) / "meta.pkl")
        store.add([np.zeros(1536, dtype=np.float32)], [{"text": "class User: pass", "file": "models.py"}])

        spec = PageSpec(title="Models", slug="models", modules=["models.py"])
        result = await generate_page(spec, store, mock_llm, mock_embedding, repo_name="test")
    assert isinstance(result, PageResult)
    assert result.slug == "models"
    assert len(result.content) > 0

async def test_generate_page_content_is_non_empty(mock_llm, mock_embedding):
    import numpy as np
    from worker.pipeline.rag_indexer import FAISSStore
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = FAISSStore(dimension=1536, index_path=Path(tmp) / "idx",
                           meta_path=Path(tmp) / "meta.pkl")
        store.add([np.zeros(1536, dtype=np.float32)], [{"text": "x = 1", "file": "main.py"}])
        spec = PageSpec(title="Overview", slug="overview", modules=["."])
        result = await generate_page(spec, store, mock_llm, mock_embedding, repo_name="test")
    assert result.content.strip() != ""
