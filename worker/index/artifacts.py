"""Filesystem artifact helpers for index jobs.

FAISS helpers (_make_faiss_store, _load_faiss_for_research) were removed in B2.5
when Stage 4 (FAISS vector indexing) was deleted from the pipeline.
The wiki indexing pipeline now uses BM25 keyword retrieval (KeywordIndex).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

LEGACY_REPO_INDEX_NAME = "fast_report_index.json"
LEGACY_FILE_ANALYSIS_SUMMARY_NAME = "file_analysis_summary.txt"


async def _write_text_async(path: Path, content: str) -> None:
    """Write a string to a file without blocking the event loop."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, path.write_text, content)


def remove_stale_ast_artifacts(ast_dir: Path) -> None:
    for name in (LEGACY_REPO_INDEX_NAME, LEGACY_FILE_ANALYSIS_SUMMARY_NAME):
        stale_path = ast_dir / name
        if stale_path.exists():
            stale_path.unlink()


def phase1_prompt_dump_path(repo_data_dir: Path) -> Path | None:
    if os.environ.get("AUTOWIKI_DEBUG_DUMP_PROMPTS") == "1":
        return repo_data_dir / "logs" / "phase1_prompt.txt"
    return None


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
