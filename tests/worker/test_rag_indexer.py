import pytest
import numpy as np
from pathlib import Path
from unittest.mock import AsyncMock
from worker.pipeline.rag_indexer import chunk_file, FAISSStore

def test_chunk_file_returns_non_empty():
    from pathlib import Path
    import tempfile
    content = "def foo():\n    return 1\n" * 50  # repeat to get multiple chunks
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(content)
        fname = Path(f.name)
    chunks = chunk_file(fname, chunk_size=200, overlap=20)
    assert len(chunks) >= 1
    assert all(isinstance(c, str) for c in chunks)

def test_chunk_file_small_file_is_one_chunk(tmp_path):
    small = tmp_path / "small.py"
    small.write_text("x = 1\ny = 2\n")
    chunks = chunk_file(small, chunk_size=1000, overlap=100)
    assert len(chunks) == 1

async def test_faiss_store_add_and_search(tmp_path):
    store = FAISSStore(dimension=4, index_path=tmp_path / "test.index",
                       meta_path=tmp_path / "test.meta.pkl")
    vecs = [np.array([1, 0, 0, 0], dtype=np.float32),
            np.array([0, 1, 0, 0], dtype=np.float32)]
    metas = [{"text": "alpha", "file": "a.py"}, {"text": "beta", "file": "b.py"}]
    store.add(vecs, metas)
    results = store.search(np.array([1, 0, 0, 0], dtype=np.float32), k=1)
    assert results[0]["text"] == "alpha"

async def test_faiss_store_persist_and_load(tmp_path):
    store = FAISSStore(dimension=4, index_path=tmp_path / "test.index",
                       meta_path=tmp_path / "test.meta.pkl")
    vecs = [np.array([1, 0, 0, 0], dtype=np.float32)]
    store.add(vecs, [{"text": "hello", "file": "x.py"}])
    store.save()

    store2 = FAISSStore(dimension=4, index_path=tmp_path / "test.index",
                        meta_path=tmp_path / "test.meta.pkl")
    store2.load()
    results = store2.search(np.array([1, 0, 0, 0], dtype=np.float32), k=1)
    assert results[0]["text"] == "hello"
