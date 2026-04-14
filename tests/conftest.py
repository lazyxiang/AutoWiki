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
    m.generate.return_value = (
        "## Overview\n\nMocked wiki page content.\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n*Source: main.py:1-5*"
    )

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
    # generate_batch must return one response per prompt; use a side_effect so
    # the length matches whatever batch size the caller requests.
    m.generate_batch.side_effect = lambda prompts, **kwargs: [
        "Mocked wiki page content." for _ in prompts
    ]
    return m


@pytest.fixture
def mock_fast_llm():
    """Returns a mock LLMProvider for the fast model (outline + fact-check passes).

    Alternates between returning a valid page outline and a passing fact-check
    so it works for any number of pages without exhausting a fixed side_effect list.
    """
    m = AsyncMock()

    _outline = {
        "sections": [
            {
                "heading": "Overview",
                "kind": "prose+diagram",
                "focus": "What it does",
                "diagram": {
                    "type": "flowchart",
                    "purpose": "Component flow",
                    "source_files": [],
                },
            }
        ],
        "key_claims": ["Claim one", "Claim two", "Claim three"],
    }
    _fact_check_pass = {"verdict": "pass", "issues": []}

    # Alternate: outline, fact-check, outline, fact-check, ...
    _call_counter = [0]

    async def _structured_side_effect(*args, **kwargs):
        idx = _call_counter[0]
        _call_counter[0] += 1
        return _outline if idx % 2 == 0 else _fact_check_pass

    m.generate_structured.side_effect = _structured_side_effect
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
