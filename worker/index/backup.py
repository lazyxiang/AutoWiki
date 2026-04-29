"""Backup and restore helpers for destructive full-index runs."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete as sa_delete
from sqlalchemy import select as sa_select

from shared.database import get_session
from shared.models import Job, Repository, WikiPage
from worker.index.artifacts import (
    _copy_file_if_exists,
    _remove_path,
    _restore_file_from_backup,
    _wiki_dir_has_content,
)

logger = logging.getLogger("worker.task")


@dataclass
class _FullIndexBackup:
    """Snapshot of the last successful wiki state before a destructive reindex."""

    backup_dir: Path
    repo_values: dict
    wiki_pages: list[dict]
    had_wiki_dir: bool
    snapshot_faiss: bool
    snapshot_plan: bool


_REPOSITORY_BACKUP_FIELDS = (
    "owner",
    "name",
    "description",
    "stars",
    "language",
    "wiki_language",
    "platform",
    "last_commit",
    "status",
    "default_branch",
    "is_private",
    "indexed_at",
    "wiki_path",
    "wiki_structure",
)

_WIKI_PAGE_BACKUP_FIELDS = (
    "id",
    "repo_id",
    "slug",
    "title",
    "content",
    "description",
    "page_order",
    "parent_slug",
    "updated_at",
)


async def _repo_has_previous_success(
    db_path: str, repo_id: str, repo_data_dir: Path
) -> bool:
    """Return whether a repo has a prior successful wiki state to protect."""
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is None:
            return False
        result = await s.execute(
            sa_select(WikiPage.id).where(WikiPage.repo_id == repo_id).limit(1)
        )
        has_page_rows = result.scalar_one_or_none() is not None
        return bool(
            repo.indexed_at
            or repo.wiki_path
            or repo.wiki_structure
            or has_page_rows
            or _wiki_dir_has_content(repo_data_dir)
        )


async def _snapshot_full_index_state(
    db_path: str,
    repo_id: str,
    repo_data_dir: Path,
    job_id: str,
    *,
    reuse_index: bool,
    reuse_plan: bool,
) -> _FullIndexBackup:
    """Snapshot DB rows and mutable artifacts before full-index cleanup."""
    backup_dir = repo_data_dir / f".full-index-backup-{job_id}"

    def _snapshot_files() -> tuple[bool, bool, bool]:
        _remove_path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)

        wiki_dir = repo_data_dir / "wiki"
        had_wiki_dir = wiki_dir.exists()
        if had_wiki_dir:
            shutil.copytree(wiki_dir, backup_dir / "wiki", symlinks=True)

        snapshot_faiss = not reuse_index
        if snapshot_faiss:
            _copy_file_if_exists(
                repo_data_dir / "faiss.index", backup_dir / "faiss.index"
            )
            _copy_file_if_exists(
                repo_data_dir / "faiss.meta.pkl", backup_dir / "faiss.meta.pkl"
            )

        snapshot_plan = not reuse_plan
        if snapshot_plan:
            _copy_file_if_exists(
                repo_data_dir / "ast" / "wiki_plan.json",
                backup_dir / "ast" / "wiki_plan.json",
            )
        _copy_file_if_exists(
            repo_data_dir / "ast" / "fast_report_index.json",
            backup_dir / "ast" / "fast_report_index.json",
        )
        return had_wiki_dir, snapshot_faiss, snapshot_plan

    loop = asyncio.get_running_loop()
    try:
        had_wiki_dir, snapshot_faiss, snapshot_plan = await loop.run_in_executor(
            None, _snapshot_files
        )

        async with get_session(db_path) as s:
            repo = await s.get(Repository, repo_id)
            if repo is None:
                raise ValueError(f"Repository not found for backup: {repo_id}")
            repo_values = {
                field: getattr(repo, field) for field in _REPOSITORY_BACKUP_FIELDS
            }
            result = await s.execute(
                sa_select(WikiPage)
                .where(WikiPage.repo_id == repo_id)
                .order_by(WikiPage.page_order)
            )
            wiki_pages = [
                {field: getattr(page, field) for field in _WIKI_PAGE_BACKUP_FIELDS}
                for page in result.scalars().all()
            ]
    except Exception:
        try:
            await loop.run_in_executor(None, _remove_path, backup_dir)
        except Exception:
            logger.warning(
                "Failed to remove partial full-index backup at %s",
                backup_dir,
                exc_info=True,
            )
        raise

    return _FullIndexBackup(
        backup_dir=backup_dir,
        repo_values=repo_values,
        wiki_pages=wiki_pages,
        had_wiki_dir=had_wiki_dir,
        snapshot_faiss=snapshot_faiss,
        snapshot_plan=snapshot_plan,
    )


async def _restore_full_index_state(
    db_path: str, repo_id: str, backup: _FullIndexBackup
) -> None:
    """Restore a previously indexed repo after a failed destructive reindex."""
    repo_data_dir = backup.backup_dir.parent

    def _restore_files() -> None:
        wiki_dir = repo_data_dir / "wiki"
        _remove_path(wiki_dir)
        if backup.had_wiki_dir:
            shutil.copytree(backup.backup_dir / "wiki", wiki_dir, symlinks=True)

        if backup.snapshot_faiss:
            _restore_file_from_backup(
                backup.backup_dir / "faiss.index",
                repo_data_dir / "faiss.index",
            )
            _restore_file_from_backup(
                backup.backup_dir / "faiss.meta.pkl",
                repo_data_dir / "faiss.meta.pkl",
            )

        if backup.snapshot_plan:
            _restore_file_from_backup(
                backup.backup_dir / "ast" / "wiki_plan.json",
                repo_data_dir / "ast" / "wiki_plan.json",
            )
        _restore_file_from_backup(
            backup.backup_dir / "ast" / "fast_report_index.json",
            repo_data_dir / "ast" / "fast_report_index.json",
        )

    await asyncio.get_running_loop().run_in_executor(None, _restore_files)

    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        if repo is not None:
            for field, value in backup.repo_values.items():
                setattr(repo, field, value)
            repo.status = "ready"

        await s.execute(sa_delete(WikiPage).where(WikiPage.repo_id == repo_id))
        for page_values in backup.wiki_pages:
            s.add(WikiPage(**page_values))
        await s.commit()


async def _cleanup_first_time_index_failure(
    db_path: str,
    repo_id: str,
    repo_data_dir: Path,
    *,
    current_job_id: str | None = None,
    reuse_index: bool,
    reuse_plan: bool,
) -> None:
    """Remove first-time failed repo metadata and generated wiki artifacts."""

    def _cleanup_files() -> None:
        _remove_path(repo_data_dir / "wiki")
        if not reuse_index:
            _remove_path(repo_data_dir / "faiss.index")
            _remove_path(repo_data_dir / "faiss.meta.pkl")
        if not reuse_plan:
            _remove_path(repo_data_dir / "ast" / "wiki_plan.json")
        _remove_path(repo_data_dir / "ast" / "fast_report_index.json")

    await asyncio.get_running_loop().run_in_executor(None, _cleanup_files)
    async with get_session(db_path) as s:
        await s.execute(sa_delete(WikiPage).where(WikiPage.repo_id == repo_id))
        job_delete = sa_delete(Job).where(Job.repo_id == repo_id)
        if current_job_id is not None:
            job_delete = job_delete.where(Job.id != current_job_id)
        await s.execute(job_delete)
        if current_job_id is None:
            await s.execute(sa_delete(Repository).where(Repository.id == repo_id))
        else:
            repo = await s.get(Repository, repo_id)
            if repo is not None:
                repo.status = "error"
        await s.commit()


async def _discard_full_index_backup(backup: _FullIndexBackup | None) -> None:
    if backup is None:
        return

    def _discard() -> None:
        _remove_path(backup.backup_dir)

    try:
        await asyncio.get_running_loop().run_in_executor(None, _discard)
    except Exception:
        logger.warning(
            "Failed to remove full-index backup at %s", backup.backup_dir, exc_info=True
        )
