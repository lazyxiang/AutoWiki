"""
Integration test: runs the full 5-stage pipeline against the fixture repo
using mocked LLM and embedding providers. Verifies pages are stored in DB.
"""

from pathlib import Path
from unittest.mock import patch

FIXTURE_REPO = Path("tests/fixtures/simple-repo")


async def test_full_pipeline_produces_pages(tmp_path, mock_llm, mock_embedding):
    import os

    os.environ["DATABASE_PATH"] = str(tmp_path / "autowiki.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)

    # mock_embedding needs a dimension attribute for FAISSStore
    mock_embedding.dimension = 1536

    from shared.config import reset_config

    reset_config()

    from shared.database import get_session, init_db

    await init_db(str(tmp_path / "autowiki.db"))

    from sqlalchemy import select

    from shared.models import Job, Repository, WikiPage

    async with get_session(str(tmp_path / "autowiki.db")) as s:
        repo = Repository(
            id="int-r1",
            owner="t",
            name="simple-repo",
            status="pending",
            platform="github",
        )
        job = Job(
            id="int-j1",
            repo_id="int-r1",
            type="full_index",
            status="queued",
            progress=0,
        )
        s.add(repo)
        s.add(job)
        await s.commit()

    with (
        patch("worker.jobs.clone_or_fetch", return_value="deadbeef"),
        patch("worker.jobs.make_llm_provider", return_value=mock_llm),
        patch("worker.jobs.make_embedding_provider", return_value=mock_embedding),
    ):
        from worker.jobs import run_full_index

        await run_full_index(
            ctx={},
            repo_id="int-r1",
            job_id="int-j1",
            owner="t",
            name="simple-repo",
            clone_root=FIXTURE_REPO,
        )

    async with get_session(str(tmp_path / "autowiki.db")) as s:
        result = await s.execute(select(WikiPage).where(WikiPage.repo_id == "int-r1"))
        pages = result.scalars().all()
        job = await s.get(Job, "int-j1")
        repo = await s.get(Repository, "int-r1")

    assert job.status == "done"
    assert repo.status == "ready"
    assert len(pages) >= 1
    assert all(p.content for p in pages)
