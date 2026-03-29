"""FastAPI application entry point for AutoWiki.

Wires routers, CORS, and database initialisation.  The ``lifespan`` context
manager handles all startup and (currently no-op) shutdown work so that the
app object itself stays declarative.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import chat as chat_router
from api.routers import jobs as jobs_router
from api.routers import repos
from api.routers import wiki as wiki_router
from api.ws import jobs as ws_jobs
from shared.config import get_config
from shared.database import init_db
from shared.logging_config import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle.

    On **startup** (code before ``yield``):
      - Reads the resolved ``Config`` via :func:`shared.config.get_config`.
      - Configures structured logging through
        :func:`shared.logging_config.setup_logging`.
      - Ensures the parent directory of the SQLite database file exists
        (creates it if necessary, including any intermediate directories).
      - Initialises the SQLite database schema via
        :func:`shared.database.init_db` — idempotent, uses ``CREATE TABLE IF
        NOT EXISTS`` internally.

    On **shutdown** (code after ``yield``):
      - No additional teardown is currently required.  Connection pools managed
        by SQLAlchemy are closed automatically when the event loop stops.

    Args:
        app (FastAPI): The FastAPI application instance passed by the framework.

    Yields:
        None: Control is yielded to FastAPI while the application is running.
    """
    cfg = get_config()
    setup_logging(cfg)
    # Guarantee the database directory tree exists before init_db tries to open
    # the file — SQLite cannot create intermediate directories itself.
    cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
    await init_db(str(cfg.database_path))
    yield


app = FastAPI(title="AutoWiki API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# Register all route groups — order matters only for OpenAPI tag ordering.
app.include_router(repos.router)
app.include_router(jobs_router.router)
app.include_router(wiki_router.router)
app.include_router(chat_router.router)
app.include_router(ws_jobs.router)
