"""Database status and progress helpers for index jobs."""

from __future__ import annotations

import asyncio

from shared.database import get_session
from shared.models import Job, Repository


async def _update_job(db_path: str, job_id: str, **kwargs) -> None:
    """Update one or more columns on a ``Job`` row in the database."""
    async with get_session(db_path) as s:
        job = await s.get(Job, job_id)
        for k, v in kwargs.items():
            setattr(job, k, v)
        await s.commit()


async def _update_repo(db_path: str, repo_id: str, **kwargs) -> None:
    """Update one or more columns on a ``Repository`` row in the database."""
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        for k, v in kwargs.items():
            setattr(repo, k, v)
        await s.commit()


def _format_active_page_status(verb: str, active_pages: dict[str, str]) -> str:
    page_states = ", ".join(
        f"{_quote_page_title(title)} [{stage}]" for title, stage in active_pages.items()
    )
    return f"{verb} active pages: {page_states}"


def _quote_page_title(title: str) -> str:
    clean_title = title.replace(chr(34), "'")
    return f'"{clean_title}"'


def _make_page_progress_callback(db_path: str, job_id: str, verb: str):
    """Return a callback that writes active page/stage progress to the job row."""
    lock = asyncio.Lock()
    active_pages: dict[str, str] = {}
    idle_status = f"{verb} wiki pages..."

    async def _on_progress(title: str, stage: str | None) -> None:
        async with lock:
            if stage is None:
                active_pages.pop(title, None)
            else:
                active_pages[title] = stage
            status_description = (
                _format_active_page_status(verb, active_pages)
                if active_pages
                else idle_status
            )
            await _update_job(
                db_path,
                job_id,
                status_description=status_description,
            )

    return _on_progress


def _make_on_retry(db_path: str, job_id: str):
    """Return an ``on_retry`` callback that writes retry status to the DB."""

    async def _on_retry(
        attempt: int, max_retries: int, wait: float, exc: Exception
    ) -> None:
        await _update_job(
            db_path,
            job_id,
            status_description=(
                f"Retry {attempt}/{max_retries} in {wait:.0f}s ({type(exc).__name__})"
            ),
        )

    return _on_retry
