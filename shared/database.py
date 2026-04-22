from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from shared.models import Base

_engines: dict[str, Any] = {}
_session_factories: dict[str, async_sessionmaker] = {}


async def init_db(database_path: str) -> None:
    url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_async_engine(url, echo=False)
    _engines[database_path] = engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add columns that may be missing in databases created before Phase 5.
        for stmt in [
            "ALTER TABLE repositories ADD COLUMN is_private BOOLEAN DEFAULT 0",
        ]:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass  # Column already exists
    _session_factories[database_path] = async_sessionmaker(
        engine, expire_on_commit=False
    )


@asynccontextmanager
async def get_session(database_path: str):
    if database_path not in _session_factories:
        raise RuntimeError(
            f"Database not initialized: {database_path!r}. Call init_db() first."
        )
    factory = _session_factories[database_path]
    async with factory() as session:
        yield session


async def dispose_db(database_path: str) -> None:
    """Dispose engine and remove caches for a database path. Use in test teardown."""
    engine = _engines.pop(database_path, None)
    _session_factories.pop(database_path, None)
    if engine is not None:
        await engine.dispose()
