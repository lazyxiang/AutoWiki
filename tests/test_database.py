import pytest
import asyncio
from pathlib import Path
from shared.database import init_db, get_session
from shared.models import Repository, Job, WikiPage

@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(str(db_path))
    return db_path

async def test_create_repository(db):
    async with get_session(str(db)) as session:
        repo = Repository(
            id="abc123",
            owner="testowner",
            name="testrepo",
            platform="github",
            status="pending",
        )
        session.add(repo)
        await session.commit()

    async with get_session(str(db)) as session:
        result = await session.get(Repository, "abc123")
        assert result.owner == "testowner"
        assert result.status == "pending"

async def test_create_job(db):
    async with get_session(str(db)) as session:
        repo = Repository(id="r1", owner="o", name="n", status="pending")
        job = Job(id="j1", repo_id="r1", type="full_index", status="queued", progress=0)
        session.add(repo)
        session.add(job)
        await session.commit()

    async with get_session(str(db)) as session:
        result = await session.get(Job, "j1")
        assert result.status == "queued"
        assert result.progress == 0
