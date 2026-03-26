from __future__ import annotations

import os

from arq import create_pool
from arq.connections import RedisSettings


async def enqueue_full_index(
    repo_id: str, job_id: str, owner: str, name: str, force: bool = False
) -> str:
    redis = await create_pool(
        RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))
    )
    await redis.enqueue_job(
        "run_full_index",
        repo_id=repo_id,
        job_id=job_id,
        owner=owner,
        name=name,
        force=force,
    )
    await redis.close()
    return job_id
