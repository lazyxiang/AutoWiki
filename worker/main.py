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
    import argparse
    import os

    from arq import run_worker
    from arq.connections import RedisSettings

    from shared.config import get_config
    from shared.logging_config import setup_logging

    parser = argparse.ArgumentParser(description="AutoWiki Worker")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    cfg = get_config()
    if args.debug:
        cfg.debug = True
    setup_logging(cfg)

    WorkerSettings.redis_settings = RedisSettings.from_dsn(
        os.environ.get("REDIS_URL", "redis://localhost:6379")
    )
    run_worker(WorkerSettings)
