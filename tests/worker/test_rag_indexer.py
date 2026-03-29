from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np

from worker.pipeline.rag_indexer import (
    FAISSStore,
    build_rag_index,
    chunk_file_with_entities,
    chunk_file_with_lines,
)


def test_chunk_file_with_lines_tracks_line_numbers(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("line1\nline2\nline3\nline4\nline5\n")
    chunks = chunk_file_with_lines(f, chunk_size=1000)
    assert len(chunks) >= 1
    assert chunks[0]["start_line"] == 1
    assert "text" in chunks[0]


def test_chunk_file_with_lines_multiple_chunks(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("x = 1\n" * 200)  # big enough to split
    chunks = chunk_file_with_lines(f, chunk_size=100, overlap=10)
    assert len(chunks) > 1
    # Each chunk should have line number info
    for c in chunks:
        assert "start_line" in c
        assert "end_line" in c
        assert c["start_line"] >= 1


def test_chunk_file_with_entities_keeps_small_entities(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    entities = [
        {"name": "foo", "type": "function", "start_line": 1, "end_line": 2},
        {"name": "bar", "type": "function", "start_line": 4, "end_line": 5},
    ]
    chunks = chunk_file_with_entities(f, entities, chunk_size=500)
    assert len(chunks) >= 2
    entity_names = [c.get("entity") for c in chunks if c.get("entity")]
    assert "foo" in entity_names
    assert "bar" in entity_names


def test_chunk_file_with_entities_falls_back_without_entities(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("x = 1\ny = 2\n")
    chunks = chunk_file_with_entities(f, [], chunk_size=500)
    assert len(chunks) >= 1


async def test_faiss_store_add_and_search(tmp_path):
    store = FAISSStore(
        dimension=4,
        index_path=tmp_path / "test.index",
        meta_path=tmp_path / "test.meta.pkl",
    )
    vecs = [
        np.array([1, 0, 0, 0], dtype=np.float32),
        np.array([0, 1, 0, 0], dtype=np.float32),
    ]
    metas = [{"text": "alpha", "file": "a.py"}, {"text": "beta", "file": "b.py"}]
    store.add(vecs, metas)
    results = store.search(np.array([1, 0, 0, 0], dtype=np.float32), k=1)
    assert results[0]["text"] == "alpha"


async def test_faiss_store_multi_search(tmp_path):
    store = FAISSStore(
        dimension=4,
        index_path=tmp_path / "test.index",
        meta_path=tmp_path / "test.meta.pkl",
    )
    vecs = [
        np.array([1, 0, 0, 0], dtype=np.float32),
        np.array([0, 1, 0, 0], dtype=np.float32),
        np.array([0, 0, 1, 0], dtype=np.float32),
    ]
    metas = [
        {"text": "alpha", "file": "a.py", "start_line": 1, "chunk_idx": 0},
        {"text": "beta", "file": "b.py", "start_line": 1, "chunk_idx": 0},
        {"text": "gamma", "file": "c.py", "start_line": 1, "chunk_idx": 0},
    ]
    store.add(vecs, metas)

    queries = [
        np.array([1, 0, 0, 0], dtype=np.float32),
        np.array([0, 0, 1, 0], dtype=np.float32),
    ]
    results = store.multi_search(queries, k=1)
    texts = {r["text"] for r in results}
    assert "alpha" in texts
    assert "gamma" in texts


async def test_faiss_store_persist_and_load(tmp_path):
    store = FAISSStore(
        dimension=4,
        index_path=tmp_path / "test.index",
        meta_path=tmp_path / "test.meta.pkl",
    )
    vecs = [np.array([1, 0, 0, 0], dtype=np.float32)]
    store.add(vecs, [{"text": "hello", "file": "x.py"}])
    store.save()

    store2 = FAISSStore(
        dimension=4,
        index_path=tmp_path / "test.index",
        meta_path=tmp_path / "test.meta.pkl",
    )
    store2.load()
    results = store2.search(np.array([1, 0, 0, 0], dtype=np.float32), k=1)
    assert results[0]["text"] == "hello"


async def test_build_rag_index(tmp_path):
    src = tmp_path / "hello.py"
    src.write_text("def hello():\n    return 'world'\n")

    store = FAISSStore(
        dimension=4,
        index_path=tmp_path / "rag.index",
        meta_path=tmp_path / "rag.meta.pkl",
    )

    mock_embed = AsyncMock()
    mock_embed.embed_batch = AsyncMock(
        side_effect=lambda texts, **kwargs: [
            np.array([1, 0, 0, 0], dtype=np.float32) for _ in texts
        ]
    )

    await build_rag_index([src], tmp_path, store, mock_embed)

    results = store.search(np.array([1, 0, 0, 0], dtype=np.float32), k=1)
    assert len(results) == 1
    assert results[0]["file"] == "hello.py"
    assert "chunk_idx" in results[0]
    assert "start_line" in results[0]


async def test_build_rag_index_with_entities(tmp_path):
    src = tmp_path / "hello.py"
    src.write_text(
        "def hello():\n    return 'world'\n\ndef goodbye():\n    return 'bye'\n"
    )

    store = FAISSStore(
        dimension=4,
        index_path=tmp_path / "rag.index",
        meta_path=tmp_path / "rag.meta.pkl",
    )

    mock_embed = AsyncMock()
    mock_embed.embed_batch = AsyncMock(
        side_effect=lambda texts, **kwargs: [
            np.array([1, 0, 0, 0], dtype=np.float32) for _ in texts
        ]
    )

    file_entities = {
        "hello.py": [
            {"name": "hello", "type": "function", "start_line": 1, "end_line": 2},
            {"name": "goodbye", "type": "function", "start_line": 4, "end_line": 5},
        ]
    }

    await build_rag_index(
        [src], tmp_path, store, mock_embed, file_entities=file_entities
    )

    results = store.search(np.array([1, 0, 0, 0], dtype=np.float32), k=5)
    assert len(results) >= 2
    # Entity-aware chunks should have entity metadata
    entities_found = [r.get("entity") for r in results if r.get("entity")]
    assert "hello" in entities_found or "goodbye" in entities_found
