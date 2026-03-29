"""Async helpers that enqueue ARQ background jobs into Redis.

Each public function in this module corresponds to one named ARQ worker task
(defined in ``worker/jobs.py``).  A fresh Redis connection pool is created
and closed for every call — this keeps the API process stateless and avoids
holding idle connections between requests.
"""

from __future__ import annotations

import os

from arq import create_pool
from arq.connections import RedisSettings


async def _enqueue(job_name: str, **kwargs) -> None:
    """Push a single job onto the ARQ Redis queue.

    Creates a new ``arq`` connection pool using the ``REDIS_URL`` environment
    variable (defaults to ``redis://localhost:6379``), enqueues the named job
    with the supplied keyword arguments, and then closes the pool regardless of
    whether the enqueue succeeded.

    A new pool is created per call intentionally: the API process is stateless
    and does not maintain a long-lived Redis connection.

    Args:
        job_name (str): The ARQ task name registered on the worker
            (e.g. ``"run_full_index"``).
        **kwargs: Arbitrary keyword arguments forwarded verbatim to the ARQ
            task function when the worker picks up the job.

    Raises:
        aioredis.RedisError: If a Redis connection cannot be established or
            the enqueue command fails.
        Exception: Any other unexpected error from the ``arq`` library is
            propagated to the caller.
    """
    # Resolve the Redis DSN from the environment; fall back to a local default
    # so the app works out-of-the-box without explicit configuration.
    redis = await create_pool(
        RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))
    )
    try:
        await redis.enqueue_job(job_name, **kwargs)
    finally:
        # Always release the pool, even when enqueue_job raises, to prevent
        # connection leaks.
        await redis.close()


async def enqueue_full_index(repo_id: str, job_id: str, owner: str, name: str) -> str:
    """Enqueue a full seven-stage wiki generation job for a repository.

    Pushes a ``run_full_index`` task to the ARQ queue.  The worker will run
    the complete pipeline: ingestion → AST analysis → dependency graph →
    RAG indexing → wiki planning → page generation → diagram synthesis.

    Args:
        repo_id (str): Hex-encoded SHA-256 repository identifier (first 16
            chars), used by the worker to locate storage paths.
        job_id (str): UUID string for the ``Job`` row already inserted in
            SQLite; the worker updates progress against this ID.
        owner (str): GitHub organisation or user name (e.g. ``"octocat"``).
        name (str): GitHub repository name (e.g. ``"hello-world"``).

    Returns:
        str: The ``job_id`` passed in, returned as-is so callers can use this
            function in an assignment without a separate variable.

    Example:
        >>> job_id = await enqueue_full_index(
        ...     repo_id="a1b2c3d4e5f6a7b8",
        ...     job_id="550e8400-e29b-41d4-a716-446655440000",
        ...     owner="octocat",
        ...     name="hello-world",
        ... )
        >>> print(job_id)
        550e8400-e29b-41d4-a716-446655440000
    """
    await _enqueue(
        "run_full_index",
        repo_id=repo_id,
        job_id=job_id,
        owner=owner,
        name=name,
    )
    return job_id


async def enqueue_refresh_index(
    repo_id: str, job_id: str, owner: str, name: str
) -> str:
    """Enqueue an incremental refresh job for an already-indexed repository.

    Pushes a ``run_refresh_index`` task to the ARQ queue.  The worker will
    re-clone (or fetch) the repository, detect changed files since the last
    indexed commit SHA, and regenerate only the affected wiki pages.

    Args:
        repo_id (str): Hex-encoded SHA-256 repository identifier (first 16
            chars).
        job_id (str): UUID string for the ``Job`` row already inserted in
            SQLite.
        owner (str): GitHub organisation or user name.
        name (str): GitHub repository name.

    Returns:
        str: The ``job_id`` passed in, returned as-is.

    Example:
        >>> job_id = await enqueue_refresh_index(
        ...     repo_id="a1b2c3d4e5f6a7b8",
        ...     job_id="660e9500-f30c-52e5-b827-557766551111",
        ...     owner="octocat",
        ...     name="hello-world",
        ... )
        >>> print(job_id)
        660e9500-f30c-52e5-b827-557766551111
    """
    await _enqueue(
        "run_refresh_index", repo_id=repo_id, job_id=job_id, owner=owner, name=name
    )
    return job_id
