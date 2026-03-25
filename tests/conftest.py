import pytest
from pathlib import Path
from unittest.mock import AsyncMock

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
            {"title": "Overview", "slug": "overview", "modules": ["."],
             "description": "High-level overview of the project architecture."},
            {"title": "Models", "slug": "models", "modules": ["models.py"],
             "description": "Data models including User and Post classes."},
            {"title": "Utils", "slug": "utils", "modules": ["utils.py"],
             "description": "Utility functions for greeting and validation."},
        ]
    }
    return m

@pytest.fixture
def mock_embedding():
    """Returns a mock EmbeddingProvider that returns zero vectors."""
    import numpy as np
    m = AsyncMock()
    m.embed.return_value = np.zeros(1536, dtype="float32")
    m.embed_batch.side_effect = lambda texts, **kwargs: [np.zeros(1536, dtype="float32") for _ in texts]
    return m
