import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "simple-repo"

@pytest.fixture
def fixture_repo_path():
    return FIXTURE_REPO

@pytest.fixture
def mock_llm():
    """Returns a mock LLMProvider that returns predictable content."""
    m = AsyncMock()
    m.generate.return_value = "Mocked wiki page content."
    m.generate_structured.return_value = {
        "pages": [
            {"title": "Overview", "slug": "overview", "modules": ["."]},
            {"title": "Models", "slug": "models", "modules": ["models.py"]},
            {"title": "Utils", "slug": "utils", "modules": ["utils.py"]},
        ]
    }
    return m

@pytest.fixture
def mock_embedding():
    """Returns a mock EmbeddingProvider that returns zero vectors."""
    import numpy as np
    m = AsyncMock()
    m.embed.return_value = np.zeros(1536, dtype="float32")
    m.embed_batch.side_effect = lambda texts: [np.zeros(1536, dtype="float32") for _ in texts]
    return m
