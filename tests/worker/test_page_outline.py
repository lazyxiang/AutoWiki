from unittest.mock import AsyncMock

import pytest

from worker.pipeline.page_outline import PageOutline, validate_outline
from worker.pipeline.wiki_planner import WikiPageSpec


def test_valid_outline_passes_validation():
    raw = {
        "sections": [
            {
                "heading": "Overview",
                "kind": "prose",
                "focus": "What it does",
                "diagram": None,
            },
            {
                "heading": "Architecture",
                "kind": "prose+diagram",
                "focus": "How it works",
                "diagram": {
                    "type": "flowchart",
                    "purpose": "Show data flow",
                    "source_files": ["src/main.py"],
                },
            },
            {
                "heading": "API Surface",
                "kind": "prose+table",
                "focus": "Public methods",
                "diagram": {
                    "type": "classDiagram",
                    "purpose": "Class relationships",
                    "source_files": ["src/models.py"],
                },
            },
        ],
        "key_claims": [
            "FAISSStore uses IndexFlatIP",
            "multi_search deduplicates by (file, start_line)",
            "Chunk size defaults to 1000",
        ],
    }
    outline = validate_outline(raw, page_files=["src/main.py", "src/models.py"])
    assert isinstance(outline, PageOutline)
    assert len(outline.sections) == 3
    assert len(outline.key_claims) == 3


def test_invalid_kind_rejected():
    raw = {
        "sections": [
            {"heading": "X", "kind": "invalid_kind", "focus": "f", "diagram": None},
        ],
        "key_claims": ["claim1", "claim2", "claim3"],
    }
    with pytest.raises(ValueError, match="kind"):
        validate_outline(raw, page_files=[])


def test_invalid_diagram_type_rejected():
    raw = {
        "sections": [
            {
                "heading": "X",
                "kind": "prose",
                "focus": "f",
                "diagram": {
                    "type": "pieDiagram",
                    "purpose": "p",
                    "source_files": ["a.py"],
                },
            },
        ],
        "key_claims": ["claim1", "claim2", "claim3"],
    }
    with pytest.raises(ValueError, match="diagram type"):
        validate_outline(raw, page_files=["a.py"])


def test_too_few_claims_rejected():
    raw = {
        "sections": [
            {"heading": "X", "kind": "prose", "focus": "f", "diagram": None},
        ],
        "key_claims": ["only one"],
    }
    with pytest.raises(ValueError, match="key_claims"):
        validate_outline(raw, page_files=[])


def test_too_many_claims_rejected():
    raw = {
        "sections": [
            {"heading": "X", "kind": "prose", "focus": "f", "diagram": None},
        ],
        "key_claims": [f"claim {i}" for i in range(10)],
    }
    with pytest.raises(ValueError, match="key_claims"):
        validate_outline(raw, page_files=[])


def test_diagram_source_files_must_be_subset():
    raw = {
        "sections": [
            {
                "heading": "X",
                "kind": "prose",
                "focus": "f",
                "diagram": {
                    "type": "flowchart",
                    "purpose": "p",
                    "source_files": ["unknown.py"],
                },
            },
        ],
        "key_claims": ["a", "b", "c"],
    }
    with pytest.raises(ValueError, match="source_files"):
        validate_outline(raw, page_files=["src/main.py"])


def test_minimum_diagram_count_enforced():
    """Pages with >=3 sections must have >=2 diagrams."""
    raw = {
        "sections": [
            {"heading": "A", "kind": "prose", "focus": "a", "diagram": None},
            {"heading": "B", "kind": "prose", "focus": "b", "diagram": None},
            {"heading": "C", "kind": "prose", "focus": "c", "diagram": None},
        ],
        "key_claims": ["a", "b", "c"],
    }
    with pytest.raises(ValueError, match="diagram"):
        validate_outline(raw, page_files=[])


async def test_generate_page_outline_with_mock_llm():
    from worker.pipeline.page_outline import generate_page_outline

    mock_fast_llm = AsyncMock()
    mock_fast_llm.generate_structured.return_value = {
        "sections": [
            {
                "heading": "Overview",
                "kind": "prose",
                "focus": "What it does",
                "diagram": None,
            },
            {
                "heading": "Architecture",
                "kind": "prose+diagram",
                "focus": "How components connect",
                "diagram": {
                    "type": "flowchart",
                    "purpose": "Component relationships",
                    "source_files": ["src/main.py"],
                },
            },
        ],
        "key_claims": [
            "Main function parses arguments",
            "Config loaded from env vars",
            "Server starts on port 3000",
        ],
    }

    spec = WikiPageSpec(
        title="Core Server",
        purpose="Main server entry point.",
        files=["src/main.py", "src/config.py"],
    )
    outline = await generate_page_outline(
        spec,
        entity_summaries="- function main()\n- class Config",
        dep_info="src/main.py -> src/config.py",
        fast_llm=mock_fast_llm,
    )
    assert len(outline.sections) == 2
    assert outline.sections[1].diagram is not None
    assert outline.sections[1].diagram.type == "flowchart"
    assert len(outline.key_claims) == 3


async def test_generate_page_outline_retries_on_validation_error():
    from worker.pipeline.page_outline import generate_page_outline

    call_count = 0

    async def structured_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"sections": [], "key_claims": []}  # invalid: empty sections
        return {
            "sections": [
                {
                    "heading": "Overview",
                    "kind": "prose",
                    "focus": "f",
                    "diagram": {
                        "type": "flowchart",
                        "purpose": "p",
                        "source_files": ["a.py"],
                    },
                },
            ],
            "key_claims": ["a", "b", "c"],
        }

    mock_fast_llm = AsyncMock()
    mock_fast_llm.generate_structured.side_effect = structured_side_effect

    spec = WikiPageSpec(title="Test", purpose="Test.", files=["a.py"])
    outline = await generate_page_outline(
        spec,
        entity_summaries="entities",
        dep_info=None,
        fast_llm=mock_fast_llm,
        max_retries=2,
    )
    assert call_count == 2
    assert len(outline.sections) == 1
