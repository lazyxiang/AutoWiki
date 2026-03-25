import pytest

from shared.database import dispose_db, get_session, init_db
from shared.models import Job, Repository, WikiPage


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


async def test_create_wiki_page(db):
    import uuid

    async with get_session(str(db)) as session:
        repo = Repository(id="r2", owner="o2", name="n2", status="pending")
        page = WikiPage(
            id=str(uuid.uuid4()),
            repo_id="r2",
            slug="overview",
            title="Overview",
            content="# Overview\n\nHello world.",
            page_order=0,
            parent_slug=None,
        )
        session.add(repo)
        session.add(page)
        await session.commit()

    async with get_session(str(db)) as session:
        from sqlalchemy import select

        result = await session.execute(select(WikiPage).where(WikiPage.repo_id == "r2"))
        page = result.scalar_one()
        assert page.slug == "overview"
        assert page.parent_slug is None
        assert page.page_order == 0

async def test_chat_models_created(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    try:
        from sqlalchemy import inspect
        from shared.database import _engines
        engine = _engines[db_path]
        async with engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        assert "chat_sessions" in tables
        assert "chat_messages" in tables
    finally:
        await dispose_db(db_path)
