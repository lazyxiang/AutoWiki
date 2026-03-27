from worker.jobs import run_full_index, run_refresh_index


async def startup(ctx):
    pass  # connection pool setup if needed


async def shutdown(ctx):
    pass


class WorkerSettings:
    functions = [run_full_index, run_refresh_index]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = None  # set from REDIS_URL env at runtime


if __name__ == "__main__":
    import os

    from arq import run_worker
    from arq.connections import RedisSettings

    WorkerSettings.redis_settings = RedisSettings.from_dsn(
        os.environ.get("REDIS_URL", "redis://localhost:6379")
    )
    run_worker(WorkerSettings)
