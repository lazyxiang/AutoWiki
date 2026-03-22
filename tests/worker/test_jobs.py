import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path


async def test_full_index_job_updates_status(tmp_path, mock_llm, mock_embedding):
    """Full pipeline runs against fixture repo and sets status=ready."""
    import os
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)

    # mock_embedding needs a dimension attribute for FAISSStore
    mock_embedding.dimension = 1536

    from shared.config import reset_config
    reset_config()

    from shared.database import init_db
    await init_db(str(tmp_path / "test.db"))

    from shared.database import get_session
    from shared.models import Repository, Job
    import uuid

    async with get_session(str(tmp_path / "test.db")) as s:
        repo = Repository(id="r1", owner="testowner", name="simple-repo",
                          platform="github", status="pending")
        job = Job(id="j1", repo_id="r1", type="full_index", status="queued", progress=0)
        s.add(repo); s.add(job); await s.commit()

    with patch("worker.jobs.clone_or_fetch", return_value="abc123def456"), \
         patch("worker.jobs.make_llm_provider", return_value=mock_llm), \
         patch("worker.jobs.make_embedding_provider", return_value=mock_embedding):
        from worker.jobs import run_full_index
        await run_full_index(
            ctx={},
            repo_id="r1",
            job_id="j1",
            owner="testowner",
            name="simple-repo",
            clone_root=Path("tests/fixtures/simple-repo"),
        )

    async with get_session(str(tmp_path / "test.db")) as s:
        job = await s.get(Job, "j1")
        repo = await s.get(Repository, "r1")
        assert job.status == "done"
        assert repo.status == "ready"
