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
            # Phase 1: outline — 2-level hierarchy (2 subsystems + 3 topic pages)
            {
                "pages": [
                    {
                        "title": "Core",
                        "purpose": "Core application logic and data models.",
                    },
                    {
                        "title": "Infrastructure",
                        "purpose": "Entry points and utility infrastructure.",
                    },
                    {
                        "title": "Models",
                        "purpose": "Data models including User and Post classes.",
                        "parent": "Core",
                    },
                    {
                        "title": "Overview",
                        "purpose": "High-level overview of the project architecture.",
                        "parent": "Infrastructure",
                    },
                    {
                        "title": "Utils",
                        "purpose": "Utility functions for greeting and validation.",
                        "parent": "Infrastructure",
                    },
                ]
            },
            # Phase 2: file selection
            {
                "selections": [
                    {"page_title": "Overview", "files": ["main.py"]},
                    {"page_title": "Models", "files": ["models.py"]},
                    {"page_title": "Utils", "files": ["utils.py"]},
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

    Returns schema-appropriate payloads based on the 'schema' kwarg:
    - assignment schema (has "assignments" property) → empty assignments
    - outline schema (has "sections" property) → valid page outline
    - fact-check schema (has "verdict" property) → passing fact-check
    Falls back to alternating outline/fact-check for unrecognised schemas.
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
    _selection_pass = {"selections": []}

    _call_counter = [0]

    async def _structured_side_effect(*args, **kwargs):
        schema = kwargs.get("schema", {})
        props = schema.get("properties", {})
        if "selections" in props:
            return _selection_pass
        if "verdict" in props:
            return _fact_check_pass
        if "sections" in props:
            return _outline
        # Fallback: alternate outline / fact-check for unrecognised schemas
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
