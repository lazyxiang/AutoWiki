from __future__ import annotations
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from shared.models import Base

_engines: dict[str, any] = {}
_session_factories: dict[str, async_sessionmaker] = {}


async def init_db(database_path: str) -> None:
    url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_async_engine(url, echo=False)
    _engines[database_path] = engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _session_factories[database_path] = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session(database_path: str):
    factory = _session_factories[database_path]
    async with factory() as session:
        yield session
