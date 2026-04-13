import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np

from worker.pipeline.page_generator import (
    PageResult,
    compute_generation_order,
    generate_page,
)
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan


def _make_store():
    from worker.pipeline.rag_indexer import FAISSStore

    tmpdir = tempfile.mkdtemp()
    store = FAISSStore(
        dimension=1536,
        index_path=Path(tmpdir) / "idx",
        meta_path=Path(tmpdir) / "meta.pkl",
    )
    store.add(
        [np.zeros(1536, dtype=np.float32)],
        [
            {
                "text": "class User: pass",
                "file": "models.py",
                "start_line": 1,
                "end_line": 1,
            }
        ],
    )
    return store


def _make_mock_fast_llm():
    """Mock fast LLM for outline + fact-check passes."""
    m = AsyncMock()
    m.generate_structured.side_effect = [
        # First call: outline
        {
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
                        "purpose": "Component flow",
                        "source_files": ["models.py"],
                    },
                },
            ],
            "key_claims": [
                "User class defines the data model",
                "Models are stored in SQLite",
                "User has an id field",
            ],
        },
        # Second call: fact-check
        {"verdict": "pass", "issues": []},
    ]
    return m


async def test_generate_page_multi_pass(mock_embedding):
    store = _make_store()
    llm = AsyncMock()
    llm.generate.return_value = (
        "## Overview\n\nThe models module defines data classes.\n\n"
        "## Architecture\n\n"
        "**Diagram: Component flow**\n\n"
        "```mermaid\nflowchart TD\n  A-->B\n```\n\n"
        "*Source: models.py:1-10*\n"
    )
    fast_llm = _make_mock_fast_llm()

    spec = WikiPageSpec(
        title="Models", purpose="Data model classes.", files=["models.py"]
    )
    result = await generate_page(
        spec,
        store,
        llm,
        fast_llm,
        mock_embedding,
        repo_name="test",
    )
    assert isinstance(result, PageResult)
    assert result.slug == "models"
    assert len(result.content) > 0
    # Verify both LLMs were called
    assert fast_llm.generate_structured.call_count == 2  # outline + fact-check
    assert llm.generate.call_count == 1  # draft only


async def test_generate_page_with_fact_check_fail_triggers_revision(mock_embedding):
    store = _make_store()
    llm = AsyncMock()
    fast_llm = AsyncMock()

    fast_llm.generate_structured.side_effect = [
        # Outline
        {
            "sections": [
                {
                    "heading": "Overview",
                    "kind": "prose",
                    "focus": "f",
                    "diagram": {
                        "type": "flowchart",
                        "purpose": "p",
                        "source_files": ["models.py"],
                    },
                },
            ],
            "key_claims": ["claim 1", "claim 2", "claim 3"],
        },
        # Fact-check: fail
        {
            "verdict": "fail",
            "issues": [
                {
                    "kind": "claim",
                    "claim": "claim 1",
                    "section": "## Overview",
                    "reason": "Not supported",
                    "suggested_fix": "Remove it",
                }
            ],
        },
    ]

    _diagram = "```mermaid\nflowchart TD\n  A-->B\n```\n\n*Source: models.py:1-10*"
    llm.generate.side_effect = [
        f"## Overview\n\n**Diagram: Flow**\n\n{_diagram}",
        f"## Overview\n\nRevised content.\n\n**Diagram: Flow**\n\n{_diagram}",
    ]

    spec = WikiPageSpec(title="Models", purpose="Test.", files=["models.py"])
    await generate_page(
        spec,
        store,
        llm,
        fast_llm,
        mock_embedding,
        repo_name="test",
    )
    assert llm.generate.call_count == 2  # draft + revision


def test_compute_generation_order_unchanged():
    """Verify compute_generation_order still works."""
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="Overview", purpose="Root."),
            WikiPageSpec(title="API", purpose="API.", parent="Overview"),
            WikiPageSpec(title="Worker", purpose="Worker.", parent="Overview"),
        ]
    )
    levels = compute_generation_order(plan)
    assert len(levels) == 2
    assert all(p.parent == "Overview" for p in levels[0])
    assert levels[1][0].title == "Overview"


def test_compute_generation_order_single_level():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="A", purpose=".", files=["a.py"]),
            WikiPageSpec(title="B", purpose=".", files=["b.py"]),
        ]
    )
    levels = compute_generation_order(plan)
    assert len(levels) == 1
    assert {p.title for p in levels[0]} == {"A", "B"}


def test_compute_generation_order_two_levels():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="Root", purpose=".", files=["r.py"]),
            WikiPageSpec(title="Child1", purpose=".", parent="Root", files=["c1.py"]),
            WikiPageSpec(title="Child2", purpose=".", parent="Root", files=["c2.py"]),
        ]
    )
    levels = compute_generation_order(plan)
    # Deepest first: children first, then root
    assert len(levels) == 2
    assert {p.title for p in levels[0]} == {"Child1", "Child2"}
    assert {p.title for p in levels[1]} == {"Root"}


def test_compute_generation_order_three_levels():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="Root", purpose=".", files=["r.py"]),
            WikiPageSpec(title="Mid", purpose=".", parent="Root", files=["m.py"]),
            WikiPageSpec(title="Leaf", purpose=".", parent="Mid", files=["l.py"]),
        ]
    )
    levels = compute_generation_order(plan)
    assert len(levels) == 3
    assert levels[0][0].title == "Leaf"
    assert levels[1][0].title == "Mid"
    assert levels[2][0].title == "Root"


def test_compute_generation_order_handles_cycle():
    """Cyclic parent references should not cause infinite recursion."""
    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="A", purpose=".", parent="B", files=["a.py"]),
            WikiPageSpec(title="B", purpose=".", parent="A", files=["b.py"]),
        ]
    )
    levels = compute_generation_order(plan)
    # Both pages should appear somewhere (treated as roots due to cycle)
    all_titles = {p.title for level in levels for p in level}
    assert all_titles == {"A", "B"}
