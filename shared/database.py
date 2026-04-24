from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    async_sessionmaker,
    create_async_engine,
)

from shared.models import Base

_engines: dict[str, Any] = {}
_session_factories: dict[str, async_sessionmaker] = {}


async def _apply_legacy_migrations(conn: AsyncConnection) -> None:
    # Keep init_db additive for existing SQLite files; new tables come from create_all.
    result = await conn.execute(text("PRAGMA table_info(repositories)"))
    columns = {row[1] for row in result.fetchall()}
    if "is_private" not in columns:
        await conn.execute(
            text("ALTER TABLE repositories ADD COLUMN is_private BOOLEAN DEFAULT 0")
        )


async def init_db(database_path: str) -> None:
    path = Path(database_path)
    if database_path != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.resolve() != Path.cwd().resolve():
            path.parent.chmod(0o700)
    url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_async_engine(url, echo=False)
    _engines[database_path] = engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_legacy_migrations(conn)
    if database_path != ":memory:":
        path.chmod(0o600)
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
