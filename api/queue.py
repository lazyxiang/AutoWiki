from __future__ import annotations

import os

from arq import create_pool
from arq.connections import RedisSettings


async def _enqueue(job_name: str, **kwargs) -> None:
    redis = await create_pool(
        RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))
    )
    try:
        await redis.enqueue_job(job_name, **kwargs)
    finally:
        await redis.close()


async def enqueue_full_index(
    repo_id: str, job_id: str, owner: str, name: str, force: bool = False
) -> str:
    await _enqueue(
        "run_full_index",
        repo_id=repo_id,
        job_id=job_id,
        owner=owner,
        name=name,
        force=force,
    )
    return job_id


async def enqueue_refresh_index(
    repo_id: str, job_id: str, owner: str, name: str
) -> str:
    await _enqueue(
        "run_refresh_index", repo_id=repo_id, job_id=job_id, owner=owner, name=name
    )
    return job_id
