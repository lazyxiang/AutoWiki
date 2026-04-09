from pathlib import Path
from unittest.mock import AsyncMock

import pytest

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "simple-repo"


@pytest.fixture
def fixture_repo_path():
    return FIXTURE_REPO


@pytest.fixture
def mock_llm():
    """Returns a mock LLMProvider that returns predictable content."""
    m = AsyncMock()
    m.generate.return_value = "Mocked wiki page content."

    _structured_responses = iter(
        [
            # Phase 1: outline
            {
                "pages": [
                    {
                        "title": "Overview",
                        "purpose": "High-level overview of the project architecture.",
                    },
                    {
                        "title": "Models",
                        "purpose": "Data models including User and Post classes.",
                    },
                    {
                        "title": "Utils",
                        "purpose": "Utility functions for greeting and validation.",
                    },
                ]
            },
            # Phase 2: file assignment
            {
                "assignments": [
                    {"file": "main.py", "page_title": "Overview"},
                    {"file": "models.py", "page_title": "Models"},
                    {"file": "utils.py", "page_title": "Utils"},
                ]
            },
        ]
    )

    _default_structured = {
        "pages": [
            {"title": "Overview", "purpose": "Fallback.", "files": ["main.py"]},
        ]
    }

    async def _structured_side_effect(*args, **kwargs):
        try:
            return next(_structured_responses)
        except StopIteration:
            return _default_structured

    m.generate_structured.side_effect = _structured_side_effect
    m.generate_batch.return_value = ["Mocked wiki page content."]
    return m


@pytest.fixture
def mock_embedding():
    """Returns a mock EmbeddingProvider that returns zero vectors."""
    import numpy as np

    m = AsyncMock()
    m.embed.return_value = np.zeros(1536, dtype="float32")
    m.embed_batch.side_effect = lambda texts, **kwargs: [
        np.zeros(1536, dtype="float32") for _ in texts
    ]
    return m
