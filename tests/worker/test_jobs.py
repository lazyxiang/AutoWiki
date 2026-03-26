from pathlib import Path
from unittest.mock import AsyncMock, patch


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
    from shared.models import Job, Repository

    async with get_session(str(tmp_path / "test.db")) as s:
        repo = Repository(
            id="r1",
            owner="testowner",
            name="simple-repo",
            platform="github",
            status="pending",
        )
        job = Job(id="j1", repo_id="r1", type="full_index", status="queued", progress=0)
        s.add(repo)
        s.add(job)
        await s.commit()

    with (
        patch("worker.jobs.clone_or_fetch", return_value="abc123def456"),
        patch("worker.jobs.make_llm_provider", return_value=mock_llm),
        patch("worker.jobs.make_embedding_provider", return_value=mock_embedding),
    ):
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


async def test_run_full_index_persists_module_tree(tmp_path, mock_llm, mock_embedding):
    import json

    from shared.database import dispose_db, get_session, init_db
    from tests.conftest import FIXTURE_REPO
    from worker.jobs import run_full_index

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)

    mock_embedding.dimension = 1536

    with (
        patch("worker.jobs.get_config") as mock_cfg,
        patch(
            "worker.jobs.clone_or_fetch", new_callable=AsyncMock, return_value="abc123"
        ),
        patch("worker.jobs.make_llm_provider", return_value=mock_llm),
        patch("worker.jobs.make_embedding_provider", return_value=mock_embedding),
        patch(
            "worker.jobs.synthesize_diagrams",
            new_callable=AsyncMock,
            return_value="graph TD\n  A-->B",
        ),
    ):
        cfg = mock_cfg.return_value
        cfg.database_path = tmp_path / "test.db"
        cfg.data_dir = tmp_path
        import uuid

        from shared.models import Job, Repository

        repo_id = "test_repo_1"
        job_id = str(uuid.uuid4())
        async with get_session(db_path) as s:
            s.add(Repository(id=repo_id, owner="o", name="r", status="pending"))
            s.add(
                Job(
                    id=job_id,
                    repo_id=repo_id,
                    type="full_index",
                    status="queued",
                    progress=0,
                )
            )
            await s.commit()
        await run_full_index(
            {},
            repo_id=repo_id,
            job_id=job_id,
            owner="o",
            name="r",
            clone_root=FIXTURE_REPO,
        )

    try:
        module_tree_path = tmp_path / "repos" / repo_id / "ast" / "module_tree.json"
        assert module_tree_path.exists()
        tree = json.loads(module_tree_path.read_text())
        assert isinstance(tree, list)

        # Verify Stage 6: diagram prepended to first wiki page in DB
        from sqlalchemy import select as sa_select

        from shared.models import WikiPage

        async with get_session(db_path) as s:
            result = await s.execute(
                sa_select(WikiPage)
                .where(WikiPage.repo_id == repo_id)
                .order_by(WikiPage.page_order)
            )
            pages = result.scalars().all()
        assert len(pages) > 0
        assert "## Architecture" in pages[0].content
        assert "```mermaid" in pages[0].content
    finally:
        await dispose_db(db_path)


async def _setup_db(tmp_path):
    """Helper: set env vars, init DB, return db_path string."""
    import os

    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)

    from shared.config import reset_config

    reset_config()

    from shared.database import init_db

    await init_db(str(tmp_path / "test.db"))
    return str(tmp_path / "test.db")


async def test_force_clears_existing_artifacts(tmp_path, mock_llm, mock_embedding):
    """force=True deletes existing FAISS files and WikiPage records."""
    mock_embedding.dimension = 1536
    db_path = await _setup_db(tmp_path)

    from shared.database import get_session
    from shared.models import Job, Repository, WikiPage

    async with get_session(db_path) as s:
        repo = Repository(
            id="r2",
            owner="testowner",
            name="simple-repo",
            platform="github",
            status="ready",
        )
        job = Job(id="j2", repo_id="r2", type="full_index", status="queued", progress=0)
        old_page = WikiPage(
            id="wp-old",
            repo_id="r2",
            slug="stale-page",
            title="Stale",
            content="old",
            page_order=0,
        )
        s.add(repo)
        s.add(job)
        s.add(old_page)
        await s.commit()

    repo_data = tmp_path / "repos" / "r2"
    repo_data.mkdir(parents=True)
    (repo_data / "faiss.index").write_bytes(b"fake")
    (repo_data / "faiss.meta.pkl").write_bytes(b"fake")
    wiki_dir = repo_data / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "stale-page.md").write_text("old content")

    with (
        patch("worker.jobs.clone_or_fetch", return_value="newsha"),
        patch("worker.jobs.make_llm_provider", return_value=mock_llm),
        patch("worker.jobs.make_embedding_provider", return_value=mock_embedding),
    ):
        from worker.jobs import run_full_index

        await run_full_index(
            ctx={},
            repo_id="r2",
            job_id="j2",
            owner="testowner",
            name="simple-repo",
            clone_root=Path("tests/fixtures/simple-repo"),
            force=True,
        )

    async with get_session(db_path) as s:
        from sqlalchemy import select

        result = await s.execute(
            select(WikiPage).where(
                WikiPage.repo_id == "r2", WikiPage.slug == "stale-page"
            )
        )
        assert result.scalar_one_or_none() is None, "stale page should be cleared"

    async with get_session(db_path) as s:
        job = await s.get(Job, "j2")
        assert job.status == "done"


async def test_resume_skips_existing_pages(tmp_path, mock_llm, mock_embedding):
    """Default (force=False) skips pages whose slugs already exist in the DB."""
    mock_embedding.dimension = 1536
    db_path = await _setup_db(tmp_path)

    from shared.database import get_session
    from shared.models import Job, Repository, WikiPage

    async with get_session(db_path) as s:
        repo = Repository(
            id="r3",
            owner="testowner",
            name="simple-repo",
            platform="github",
            status="ready",
        )
        job = Job(id="j3", repo_id="r3", type="full_index", status="queued", progress=0)
        existing_page = WikiPage(
            id="wp-overview",
            repo_id="r3",
            slug="overview",
            title="Overview",
            content="existing overview content",
            page_order=0,
        )
        s.add(repo)
        s.add(job)
        s.add(existing_page)
        await s.commit()

    generate_page_calls: list[str] = []

    async def mock_generate_page(spec, *args, **kwargs):
        generate_page_calls.append(spec.slug)
        from worker.pipeline.page_generator import PageResult

        return PageResult(slug=spec.slug, title=spec.title, content="generated")

    with (
        patch("worker.jobs.clone_or_fetch", return_value="abc"),
        patch("worker.jobs.make_llm_provider", return_value=mock_llm),
        patch("worker.jobs.make_embedding_provider", return_value=mock_embedding),
        patch("worker.jobs.generate_page", side_effect=mock_generate_page),
    ):
        from worker.jobs import run_full_index

        await run_full_index(
            ctx={},
            repo_id="r3",
            job_id="j3",
            owner="testowner",
            name="simple-repo",
            clone_root=Path("tests/fixtures/simple-repo"),
            force=False,
        )

    assert "overview" not in generate_page_calls, (
        "overview page should have been skipped in resume mode"
    )

    async with get_session(db_path) as s:
        job = await s.get(Job, "j3")
        assert job.status == "done"
