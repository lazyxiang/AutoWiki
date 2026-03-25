from pathlib import Path

from worker.pipeline.page_generator import PageResult, generate_page
from worker.pipeline.wiki_planner import PageSpec


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

        spec = PageSpec(
            title="Models",
            slug="models",
            modules=["models.py"],
            description="Data model classes.",
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

        spec = PageSpec(
            title="Models",
            slug="models",
            modules=["models.py"],
            description="User and Post data models.",
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
        spec = PageSpec(title="Overview", slug="overview", modules=["."])
        result = await generate_page(
            spec, store, mock_llm, mock_embedding, repo_name="test"
        )
    assert result.content.strip() != ""
