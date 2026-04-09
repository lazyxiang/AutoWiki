from pathlib import Path

from worker.pipeline.page_generator import (
    PageResult,
    compute_generation_order,
    generate_page,
)
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan


async def test_generate_page_returns_markdown(mock_llm, mock_embedding):
    import tempfile

    import numpy as np

    from worker.pipeline.rag_indexer import FAISSStore

    with tempfile.TemporaryDirectory() as tmp:
        store = FAISSStore(
            dimension=1536,
            index_path=Path(tmp) / "idx",
            meta_path=Path(tmp) / "meta.pkl",
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

        spec = WikiPageSpec(
            title="Models",
            purpose="Data model classes.",
            files=["models.py"],
        )
        result = await generate_page(
            spec, store, mock_llm, mock_embedding, repo_name="test"
        )
    assert isinstance(result, PageResult)
    assert result.slug == "models"
    assert len(result.content) > 0


async def test_generate_page_with_dep_info_and_entities(mock_llm, mock_embedding):
    import tempfile

    import numpy as np

    from worker.pipeline.rag_indexer import FAISSStore

    with tempfile.TemporaryDirectory() as tmp:
        store = FAISSStore(
            dimension=1536,
            index_path=Path(tmp) / "idx",
            meta_path=Path(tmp) / "meta.pkl",
        )
        store.add(
            [np.zeros(1536, dtype=np.float32)],
            [
                {
                    "text": "class User: pass",
                    "file": "models.py",
                    "start_line": 1,
                    "end_line": 5,
                }
            ],
        )

        spec = WikiPageSpec(
            title="Models",
            purpose="User and Post data models.",
            files=["models.py"],
        )
        dep_info = {
            "depends_on": ["utils"],
            "depended_by": ["api"],
            "external_deps": ["sqlalchemy"],
        }
        entity_details = [
            {
                "type": "class",
                "name": "User",
                "signature": "User(Base)",
                "file": "models.py",
                "start_line": 1,
                "end_line": 10,
                "docstring": "Represents a user account.",
            },
        ]
        result = await generate_page(
            spec,
            store,
            mock_llm,
            mock_embedding,
            repo_name="test",
            dep_info=dep_info,
            entity_details=entity_details,
        )
    assert isinstance(result, PageResult)
    assert result.content.strip() != ""


async def test_generate_page_content_is_non_empty(mock_llm, mock_embedding):
    import tempfile

    import numpy as np

    from worker.pipeline.rag_indexer import FAISSStore

    with tempfile.TemporaryDirectory() as tmp:
        store = FAISSStore(
            dimension=1536,
            index_path=Path(tmp) / "idx",
            meta_path=Path(tmp) / "meta.pkl",
        )
        store.add(
            [np.zeros(1536, dtype=np.float32)], [{"text": "x = 1", "file": "main.py"}]
        )
        spec = WikiPageSpec(
            title="Overview", purpose="High-level overview.", files=["main.py"]
        )
        result = await generate_page(
            spec, store, mock_llm, mock_embedding, repo_name="test"
        )
    assert result.content.strip() != ""


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


async def test_generate_page_with_child_contents(mock_llm, mock_embedding):
    import tempfile

    import numpy as np

    from worker.pipeline.rag_indexer import FAISSStore

    with tempfile.TemporaryDirectory() as tmp:
        store = FAISSStore(
            dimension=1536,
            index_path=Path(tmp) / "idx",
            meta_path=Path(tmp) / "meta.pkl",
        )
        store.add(
            [np.zeros(1536, dtype=np.float32)],
            [
                {
                    "text": "class App: pass",
                    "file": "app.py",
                    "start_line": 1,
                    "end_line": 1,
                }
            ],
        )

        parent_spec = WikiPageSpec(
            title="System Overview",
            purpose="Top-level architecture.",
            files=["app.py"],
        )
        child_results = [
            PageResult(
                slug="api",
                title="API Layer",
                content="## API\nHandles HTTP requests.",
            ),
            PageResult(
                slug="worker",
                title="Worker",
                content="## Worker\nProcesses background jobs.",
            ),
        ]
        result = await generate_page(
            parent_spec,
            store,
            mock_llm,
            mock_embedding,
            repo_name="test",
            child_contents=child_results,
        )
    assert isinstance(result, PageResult)
    assert result.slug == "system-overview"
    # The mock returns "Mocked wiki page content." but the important thing
    # is that generate() was called with a prompt containing child content
    call_args = mock_llm.generate.call_args
    prompt = call_args[0][0]
    assert "API Layer" in prompt
    assert "Worker" in prompt
    assert "Handles HTTP requests" in prompt
