"""ARQ worker entry point.

Registers job functions and provides CLI for starting the worker process.
When invoked directly (``python -m worker.main``), it parses CLI arguments,
configures logging, wires up Redis settings from the ``REDIS_URL`` environment
variable, and hands control to ARQ's ``run_worker``.
"""

from worker.jobs import run_full_index, run_refresh_index


async def startup(ctx):
    """ARQ on_startup hook — called once when the worker process initialises.

    Use this hook to open shared resources (e.g. database connection pools,
    HTTP client sessions) that should persist across job executions and be
    stored on the shared ``ctx`` dict.  Currently a no-op; reserved for
    future connection-pool setup.

    Args:
        ctx (dict): Mutable ARQ context dictionary shared across all jobs
            running in this worker process.
    """
    pass  # connection pool setup if needed


async def shutdown(ctx):
    """ARQ on_shutdown hook — called once when the worker process is stopping.

    Use this hook to close any resources opened in ``startup`` (e.g. flush
    connection pools, close HTTP sessions).  Currently a no-op.

    Args:
        ctx (dict): Mutable ARQ context dictionary shared across all jobs
            running in this worker process.
    """
    pass


class WorkerSettings:
    """ARQ worker configuration.

    ARQ reads class attributes directly; no instantiation occurs.

    Registered job functions:
        - ``run_full_index``: Full 7-stage wiki generation pipeline for a
          repository (clone → AST → deps → RAG → plan → pages → diagram).
        - ``run_refresh_index``: Incremental refresh that re-runs the pipeline
          only for pages whose source files have changed since the last index.

    Attributes:
        functions (list): Job callables registered with the ARQ broker.
        on_startup (coroutine): Called once at worker startup.
        on_shutdown (coroutine): Called once at worker shutdown.
        redis_settings: Overridden at runtime from ``REDIS_URL``; ``None``
            defaults to ``redis://localhost:6379``.
        job_timeout (int): Set to 7200s (2 hours) as a generous upper bound.
            Each pipeline stage uses ``async_retry`` for its own timeout/retry
            logic, so this acts as a safety net rather than the primary timeout.
    """

    functions = [run_full_index, run_refresh_index]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = None  # set from REDIS_URL env at runtime
    # Set a generous timeout (2 hours) — per-call retries in async_retry handle
    # finer-grained timeouts.  ARQ requires a non-None value for max() calculation.
    job_timeout = 7200


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
