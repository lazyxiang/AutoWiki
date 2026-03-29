import json
import uuid
from unittest.mock import AsyncMock, patch


async def test_run_refresh_index_no_changes(tmp_path, mock_llm, mock_embedding):
    """If HEAD SHA == stored last_commit, job completes with status done immediately."""
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Job, Repository
    from worker.jobs import run_refresh_index

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    repo_id = "refresh_repo"
    job_id = str(uuid.uuid4())
    async with get_session(db_path) as s:
        s.add(
            Repository(
                id=repo_id, owner="o", name="r", status="ready", last_commit="abc123"
            )
        )
        s.add(
            Job(id=job_id, repo_id=repo_id, type="refresh", status="queued", progress=0)
        )
        await s.commit()

    with (
        patch("worker.jobs.get_config") as mock_cfg,
        patch(
            "worker.jobs.clone_or_fetch", new_callable=AsyncMock, return_value="abc123"
        ),
    ):
        cfg = mock_cfg.return_value
        cfg.database_path = tmp_path / "test.db"
        cfg.data_dir = tmp_path
        await run_refresh_index(
            {},
            repo_id=repo_id,
            job_id=job_id,
            owner="o",
            name="r",
            clone_root=tmp_path / "clone",
        )

    async with get_session(db_path) as s:
        job = await s.get(Job, job_id)
        assert job.status == "done"
        assert job.progress == 100
    await dispose_db(db_path)


async def test_run_refresh_index_with_changes(tmp_path, mock_llm, mock_embedding):
    """Changed files trigger re-indexing of affected modules."""
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Job, Repository, WikiPage
    from tests.conftest import FIXTURE_REPO
    from worker.jobs import run_refresh_index

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    repo_id = "refresh_repo_2"
    job_id = str(uuid.uuid4())
    old_sha = "old123"
    new_sha = "new456"

    mock_embedding.dimension = 1536

    async with get_session(db_path) as s:
        s.add(
            Repository(
                id=repo_id, owner="o", name="r", status="ready", last_commit=old_sha
            )
        )
        s.add(
            Job(id=job_id, repo_id=repo_id, type="refresh", status="queued", progress=0)
        )
        # Pre-existing wiki page for the affected module
        s.add(
            WikiPage(
                id="p1",
                repo_id=repo_id,
                slug="overview",
                title="Overview",
                content="old content",
                page_order=0,
            )
        )
        await s.commit()

    # Write a wiki_plan.json so the refresh can read it
    ast_dir = tmp_path / "repos" / repo_id / "ast"
    ast_dir.mkdir(parents=True)
    (ast_dir / "wiki_plan.json").write_text(
        json.dumps(
            {
                "repo_notes": [{"content": ""}],
                "pages": [
                    {
                        "title": "Overview",
                        "purpose": "High-level overview.",
                        "files": ["main.py"],
                    }
                ],
            }
        )
    )

    with (
        patch("worker.jobs.get_config") as mock_cfg,
        patch(
            "worker.jobs.clone_or_fetch", new_callable=AsyncMock, return_value=new_sha
        ),
        patch(
            "worker.jobs.get_changed_files",
            new_callable=AsyncMock,
            return_value=["main.py"],
        ),
        patch("worker.jobs.make_llm_provider", return_value=mock_llm),
        patch("worker.jobs.make_embedding_provider", return_value=mock_embedding),
        patch(
            "worker.jobs.synthesize_diagrams", new_callable=AsyncMock, return_value=None
        ),
    ):
        cfg = mock_cfg.return_value
        cfg.database_path = tmp_path / "test.db"
        cfg.data_dir = tmp_path
        await run_refresh_index(
            {},
            repo_id=repo_id,
            job_id=job_id,
            owner="o",
            name="r",
            clone_root=FIXTURE_REPO,
        )

    async with get_session(db_path) as s:
        job = await s.get(Job, job_id)
        assert job.status == "done"
        repo = await s.get(Repository, repo_id)
        assert repo.last_commit == new_sha
    await dispose_db(db_path)
