from worker.pipeline.rag_indexer import Chunk  # still present in B1
from worker.pipeline.retrieval.chunk import Chunk as CanonicalChunk
from worker.pipeline.retrieval.keyword_index import KeywordIndex


def test_search_returns_top_k_for_single_query():
    chunks = [
        Chunk(
            file="a.py",
            text="dependency graph build_graph",
            line_start=1,
            line_end=2,
        ),
        Chunk(file="b.py", text="wiki planner phase 2", line_start=1, line_end=2),
        Chunk(file="c.py", text="fact check verdict", line_start=1, line_end=2),
    ]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["dependency graph"], k=1)
    assert out[0].file == "a.py"


def test_search_applies_per_file_quota():
    chunks = [
        Chunk(file="a.py", text="x" * 20, line_start=i, line_end=i) for i in range(10)
    ]
    chunks += [
        Chunk(file="b.py", text="x" * 20, line_start=i, line_end=i) for i in range(10)
    ]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["x"], k=10, files=["a.py", "b.py"], per_file_quota=2)
    counts = {"a.py": 0, "b.py": 0}
    for c in out:
        counts[c.file] += 1
    assert counts["a.py"] == 2
    assert counts["b.py"] == 2


def test_search_files_filter_restricts_scope():
    chunks = [
        Chunk(file="a.py", text="alpha", line_start=1, line_end=1),
        Chunk(file="b.py", text="alpha", line_start=1, line_end=1),
    ]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["alpha"], k=5, files=["a.py"])
    assert all(c.file == "a.py" for c in out)


def test_build_empty_chunks_returns_empty_index():
    idx = KeywordIndex.build([], repo_index={"files": []})
    assert idx.search(["anything"], k=5) == []


def test_build_only_short_text_returns_empty_index():
    """All-short text means every chunk tokenizes to empty (min_ascii_len=3)."""
    chunks = [CanonicalChunk(file="a.py", text="ab", line_start=1, line_end=1)]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    assert idx.search(["anything"], k=5) == []


def test_search_hard_cap_returns_fewer_than_k():
    chunks = [
        CanonicalChunk(file="a.py", text="hello", line_start=i, line_end=i)
        for i in range(5)
    ]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["hello"], k=10, files=["a.py"], per_file_quota=2)
    assert len(out) == 2  # quota caps before k
