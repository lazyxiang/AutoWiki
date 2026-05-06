"""Filesystem and FAISS artifact helpers for index jobs."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from worker.pipeline.retrieval.rag_indexer import FAISSStore


async def _write_text_async(path: Path, content: str) -> None:
    """Write a string to a file without blocking the event loop."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, path.write_text, content)


def _make_faiss_store(repo_data_dir: Path, embedding) -> FAISSStore:
    return FAISSStore(
        dimension=embedding.dimension,
        index_path=repo_data_dir / "faiss.index",
        meta_path=repo_data_dir / "faiss.meta.pkl",
    )


async def _load_faiss_for_research(repo_data_dir: Path, embedding) -> FAISSStore:
    """Load the FAISS store for a repo, running the blocking IO in an executor."""
    store = _make_faiss_store(repo_data_dir, embedding)
    await asyncio.get_running_loop().run_in_executor(None, store.load)
    return store


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _copy_file_if_exists(src: Path, dest: Path) -> None:
    if src.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _restore_file_from_backup(src: Path, dest: Path) -> None:
    _remove_path(dest)
    if src.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _wiki_dir_has_content(repo_data_dir: Path) -> bool:
    wiki_dir = repo_data_dir / "wiki"
    return wiki_dir.is_dir() and any(wiki_dir.iterdir())
