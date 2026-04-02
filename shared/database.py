from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
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
        await conn.run_sync(_apply_migrations)
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


def _apply_migrations(connection) -> None:
    """Detect and apply missing columns for schema evolution."""
    insp = inspect(connection)
    if insp.has_table("wiki_pages"):
        columns = {col["name"] for col in insp.get_columns("wiki_pages")}
        if "description" not in columns:
            try:
                connection.execute(
                    text("ALTER TABLE wiki_pages ADD COLUMN description TEXT")
                )
            except OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    # repositories migrations
    if insp.has_table("repositories"):
        columns = {col["name"] for col in insp.get_columns("repositories")}
        if "wiki_structure" not in columns:
            try:
                connection.execute(
                    text("ALTER TABLE repositories ADD COLUMN wiki_structure TEXT")
                )
            except OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        for col_name, col_type in [
            ("description", "TEXT"),
            ("stars", "INTEGER"),
            ("language", "VARCHAR"),
        ]:
            if col_name not in columns:
                try:
                    connection.execute(
                        text(
                            f"ALTER TABLE repositories ADD COLUMN {col_name} {col_type}"
                        )
                    )
                except OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise

    # jobs migrations
    if insp.has_table("jobs"):
        columns = {col["name"] for col in insp.get_columns("jobs")}
        if "status_description" not in columns:
            try:
                connection.execute(
                    text("ALTER TABLE jobs ADD COLUMN status_description TEXT")
                )
            except OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise


async def dispose_db(database_path: str) -> None:
    """Dispose engine and remove caches for a database path. Use in test teardown."""
    engine = _engines.pop(database_path, None)
    _session_factories.pop(database_path, None)
    if engine is not None:
        await engine.dispose()
