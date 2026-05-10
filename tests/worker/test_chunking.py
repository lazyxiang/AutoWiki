"""Tests for worker.pipeline.retrieval.chunking — chunking primitives.

Ported from the deleted tests/worker/test_rag_indexer.py (B2.5).
FAISS-specific tests dropped; chunking logic lives in chunking.py.
"""

from worker.pipeline.retrieval.chunking import (
    build_chunks,
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


def test_build_chunks_returns_chunk_objects(tmp_path):
    """build_chunks wraps chunking primitives and returns Chunk dataclasses."""
    from worker.pipeline.retrieval.chunk import Chunk

    src = tmp_path / "hello.py"
    src.write_text("def hello():\n    return 'world'\n")

    chunks = build_chunks([src], tmp_path)
    assert len(chunks) >= 1
    assert all(isinstance(c, Chunk) for c in chunks)
    assert all(c.file == "hello.py" for c in chunks)


def test_build_chunks_uses_entity_aware_chunking(tmp_path):
    """build_chunks uses entity-aware chunking when file_entities provided."""
    from worker.pipeline.retrieval.chunk import Chunk

    src = tmp_path / "mod.py"
    src.write_text(
        "def hello():\n    return 'world'\n\ndef goodbye():\n    return 'bye'\n"
    )
    file_entities = {
        "mod.py": [
            {"name": "hello", "type": "function", "start_line": 1, "end_line": 2},
            {"name": "goodbye", "type": "function", "start_line": 4, "end_line": 5},
        ]
    }

    chunks = build_chunks([src], tmp_path, file_entities=file_entities)
    assert len(chunks) >= 2
    assert all(isinstance(c, Chunk) for c in chunks)


def test_build_chunks_skips_unreadable_files(tmp_path):
    """Files that do not exist are silently skipped."""

    missing = tmp_path / "nonexistent.py"
    chunks = build_chunks([missing], tmp_path)
    assert chunks == []
