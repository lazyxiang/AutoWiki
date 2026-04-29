from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker.platform.base import RepoMetadata

_FAKE_META = RepoMetadata(
    owner="o",
    name="r",
    description="",
    stars=0,
    language="",
    default_branch="main",
    is_private=False,
)
_FAKE_PLATFORM = MagicMock(
    fetch_metadata=AsyncMock(return_value=_FAKE_META),
    authenticated_clone_url=MagicMock(return_value="https://github.com/o/r.git"),
)
_PLATFORM_PATCHES = [
    ("worker.index.full.get_platform_token", dict(new=AsyncMock(return_value=None))),
    ("worker.index.full.get_platform_by_name", dict(return_value=_FAKE_PLATFORM)),
]


def test_repo_metadata_updates_skip_incomplete_fallback_values():
    from worker.jobs import _repo_metadata_updates

    meta = RepoMetadata(
        owner="o",
        name="r",
        description="",
        stars=0,
        language="",
        default_branch="main",
        is_private=False,
        complete=False,
    )
    updates = _repo_metadata_updates(meta, active_branch="main")

    assert updates == {"default_branch": "main"}


def test_repo_metadata_updates_include_complete_falsy_metadata():
    from worker.jobs import _repo_metadata_updates

    assert _repo_metadata_updates(_FAKE_META, active_branch="main") == {
        "description": "",
        "stars": 0,
        "language": "",
        "default_branch": "main",
        "is_private": False,
    }


def test_worker_jobs_reexports_split_index_entrypoints():
    from worker.index import jobs as index_jobs
    from worker.jobs import run_full_index, run_refresh_index

    assert run_full_index is index_jobs.run_full_index
    assert run_refresh_index is index_jobs.run_refresh_index


def _enter_platform_patches(stack: ExitStack) -> None:
    """Enter platform adapter patches using an ExitStack."""
    for target, kwargs in _PLATFORM_PATCHES:
        stack.enter_context(patch(target, **kwargs))


async def test_full_index_job_updates_status(
    tmp_path, mock_llm, mock_fast_llm, mock_embedding
):
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

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "worker.index.full.clone_or_fetch",
                return_value=("abc123def456", "main"),
            )
        )
        _enter_platform_patches(stack)
        stack.enter_context(
            patch("worker.index.full.make_llm_provider", return_value=mock_llm)
        )
        stack.enter_context(
            patch(
                "worker.index.full.make_fast_llm_provider", return_value=mock_fast_llm
            )
        )
        stack.enter_context(
            patch(
                "worker.index.full.make_embedding_provider", return_value=mock_embedding
            )
        )
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


async def test_run_full_index_persists_wiki_plan(
    tmp_path, mock_llm, mock_fast_llm, mock_embedding
):
    import json

    from shared.database import dispose_db, get_session, init_db
    from tests.conftest import FIXTURE_REPO
    from worker.jobs import run_full_index

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)

    mock_embedding.dimension = 1536

    with ExitStack() as stack:
        mock_cfg = stack.enter_context(patch("worker.index.full.get_config"))
        stack.enter_context(
            patch(
                "worker.index.full.clone_or_fetch",
                new_callable=AsyncMock,
                return_value=("abc123", "main"),
            )
        )
        _enter_platform_patches(stack)
        stack.enter_context(
            patch("worker.index.full.make_llm_provider", return_value=mock_llm)
        )
        stack.enter_context(
            patch(
                "worker.index.full.make_fast_llm_provider", return_value=mock_fast_llm
            )
        )
        stack.enter_context(
            patch(
                "worker.index.full.make_embedding_provider", return_value=mock_embedding
            )
        )
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
        wiki_plan_path = tmp_path / "repos" / repo_id / "ast" / "wiki_plan.json"
        assert wiki_plan_path.exists()
        plan_data = json.loads(wiki_plan_path.read_text())
        assert "pages" in plan_data
        assert "repo_notes" in plan_data
        assert isinstance(plan_data["pages"], list)
        # Internal format must include files for each page
        for page in plan_data["pages"]:
            assert "files" in page, f"page {page.get('title')} missing 'files'"

        # User-facing wiki.json must exist and must NOT contain files
        wiki_json_path = tmp_path / "repos" / repo_id / "wiki" / "wiki.json"
        assert wiki_json_path.exists()
        wiki_json_data = json.loads(wiki_json_path.read_text())
        assert "pages" in wiki_json_data
        assert "repo_notes" in wiki_json_data
        for page in wiki_json_data["pages"]:
            assert "files" not in page, (
                f"wiki.json page {page.get('title')} should not contain 'files'"
            )
            assert "purpose" in page

        # Verify Stage 6: wiki pages written to DB
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
        assert all(p.content for p in pages)
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


async def test_always_clears_existing_artifacts(
    tmp_path, mock_llm, mock_fast_llm, mock_embedding
):
    """run_full_index always clears existing FAISS files and WikiPage records."""
    from shared.database import dispose_db

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

    with ExitStack() as stack:
        stack.enter_context(
            patch("worker.index.full.clone_or_fetch", return_value=("newsha", "main"))
        )
        _enter_platform_patches(stack)
        stack.enter_context(
            patch("worker.index.full.make_llm_provider", return_value=mock_llm)
        )
        stack.enter_context(
            patch(
                "worker.index.full.make_fast_llm_provider", return_value=mock_fast_llm
            )
        )
        stack.enter_context(
            patch(
                "worker.index.full.make_embedding_provider", return_value=mock_embedding
            )
        )
        from worker.jobs import run_full_index

        await run_full_index(
            ctx={},
            repo_id="r2",
            job_id="j2",
            owner="testowner",
            name="simple-repo",
            clone_root=Path("tests/fixtures/simple-repo"),
        )

    try:
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
        assert not (repo_data / ".full-index-backup-j2").exists()
    finally:
        await dispose_db(db_path)


async def test_full_index_first_time_failure_preserves_failure_metadata(tmp_path):
    """Failed first-time index removes wiki rows but keeps failure metadata."""
    from shared.database import dispose_db, get_session
    from shared.models import Job, Repository, WikiPage

    db_path = await _setup_db(tmp_path)

    async with get_session(db_path) as s:
        s.add(
            Repository(
                id="first-fail",
                owner="testowner",
                name="simple-repo",
                platform="github",
                status="pending",
            )
        )
        s.add(
            Job(
                id="first-job",
                repo_id="first-fail",
                type="full_index",
                status="queued",
                progress=0,
            )
        )
        s.add(
            Job(
                id="stale-job",
                repo_id="first-fail",
                type="refresh",
                status="running",
                progress=50,
            )
        )
        await s.commit()

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "worker.index.full.clone_or_fetch",
                new_callable=AsyncMock,
                side_effect=RuntimeError("clone failed"),
            )
        )
        _enter_platform_patches(stack)

        from worker.jobs import run_full_index

        with pytest.raises(RuntimeError, match="clone failed"):
            await run_full_index(
                ctx={},
                repo_id="first-fail",
                job_id="first-job",
                owner="testowner",
                name="simple-repo",
                clone_root=Path("tests/fixtures/simple-repo"),
            )

    try:
        async with get_session(db_path) as s:
            repo = await s.get(Repository, "first-fail")
            assert repo is not None
            assert repo.status == "error"
            job = await s.get(Job, "first-job")
            assert job is not None
            assert job.status == "failed"
            assert job.error == "clone failed"
            assert await s.get(Job, "stale-job") is None

            from sqlalchemy import select

            result = await s.execute(
                select(WikiPage).where(WikiPage.repo_id == "first-fail")
            )
            assert result.scalars().all() == []
    finally:
        await dispose_db(db_path)


async def test_full_index_restore_failure_preserves_original_error(
    tmp_path, mock_embedding
):
    """Restore cleanup failures are logged without replacing the pipeline error."""
    from shared.database import dispose_db, get_session
    from shared.models import Job, Repository, WikiPage

    mock_embedding.dimension = 1536
    db_path = await _setup_db(tmp_path)

    async with get_session(db_path) as s:
        s.add(
            Repository(
                id="restore-mask",
                owner="testowner",
                name="simple-repo",
                platform="github",
                status="ready",
                indexed_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )
        s.add(
            Job(
                id="restore-mask-job",
                repo_id="restore-mask",
                type="full_index",
                status="queued",
                progress=0,
            )
        )
        s.add(
            WikiPage(
                id="restore-mask-page",
                repo_id="restore-mask",
                slug="old",
                title="Old",
                content="old content",
                page_order=0,
            )
        )
        await s.commit()

    repo_data = tmp_path / "repos" / "restore-mask"
    wiki_dir = repo_data / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "old.md").write_text("old file")

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "worker.index.full.clone_or_fetch",
                new_callable=AsyncMock,
                return_value=("newsha", "main"),
            )
        )
        _enter_platform_patches(stack)
        stack.enter_context(
            patch(
                "worker.index.full.make_embedding_provider", return_value=mock_embedding
            )
        )
        stack.enter_context(
            patch(
                "worker.index.full.analyze_all_files",
                side_effect=RuntimeError("analysis failed"),
            )
        )
        restore = stack.enter_context(
            patch(
                "worker.index.full._restore_full_index_state",
                new_callable=AsyncMock,
                side_effect=RuntimeError("restore failed"),
            )
        )
        discard = stack.enter_context(
            patch(
                "worker.index.full._discard_full_index_backup", new_callable=AsyncMock
            )
        )

        from worker.jobs import run_full_index

        with pytest.raises(RuntimeError, match="analysis failed"):
            await run_full_index(
                ctx={},
                repo_id="restore-mask",
                job_id="restore-mask-job",
                owner="testowner",
                name="simple-repo",
                clone_root=Path("tests/fixtures/simple-repo"),
            )

    try:
        restore.assert_awaited_once()
        discard.assert_awaited_once()
        async with get_session(db_path) as s:
            job = await s.get(Job, "restore-mask-job")
            assert job.status == "failed"
            assert job.error == "analysis failed"
    finally:
        await dispose_db(db_path)


async def test_snapshot_full_index_state_removes_partial_backup_on_failure(tmp_path):
    """Snapshot setup cleans up copied files when DB snapshot collection fails."""
    from shared.database import dispose_db
    from worker.index.full import _snapshot_full_index_state

    db_path = await _setup_db(tmp_path)
    repo_data = tmp_path / "repos" / "missing-repo"
    wiki_dir = repo_data / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "old.md").write_text("old file")

    with pytest.raises(ValueError, match="Repository not found"):
        await _snapshot_full_index_state(
            db_path,
            "missing-repo",
            repo_data,
            "snapshot-job",
            reuse_index=False,
            reuse_plan=False,
        )

    try:
        assert not (repo_data / ".full-index-backup-snapshot-job").exists()
    finally:
        await dispose_db(db_path)


async def test_full_index_previously_indexed_failure_restores_db_and_files(
    tmp_path, mock_embedding
):
    """Failed reindex restores the last successful wiki state and ready status."""
    from shared.database import dispose_db, get_session
    from shared.models import Job, Repository, WikiPage

    mock_embedding.dimension = 1536
    db_path = await _setup_db(tmp_path)
    indexed_at = datetime(2026, 1, 2, tzinfo=UTC)

    async with get_session(db_path) as s:
        s.add(
            Repository(
                id="restore-repo",
                owner="testowner",
                name="simple-repo",
                description="old description",
                stars=7,
                language="Python",
                platform="github",
                last_commit="oldsha",
                status="ready",
                default_branch="main",
                is_private=False,
                indexed_at=indexed_at,
                wiki_path=str(tmp_path / "repos" / "restore-repo" / "wiki"),
                wiki_structure='{"pages":[{"title":"Old"}]}',
            )
        )
        s.add(
            Job(
                id="restore-job",
                repo_id="restore-repo",
                type="full_index",
                status="queued",
                progress=0,
            )
        )
        s.add(
            WikiPage(
                id="old-page-id",
                repo_id="restore-repo",
                slug="old-page",
                title="Old Page",
                content="old db content",
                description="old page description",
                page_order=3,
                parent_slug="parent",
            )
        )
        await s.commit()

    repo_data = tmp_path / "repos" / "restore-repo"
    wiki_dir = repo_data / "wiki"
    ast_dir = repo_data / "ast"
    wiki_dir.mkdir(parents=True)
    ast_dir.mkdir()
    (wiki_dir / "old-page.md").write_text("old file content")
    (wiki_dir / "wiki.json").write_text('{"pages":[{"title":"Old"}]}')
    (repo_data / "faiss.index").write_bytes(b"old-index")
    (repo_data / "faiss.meta.pkl").write_bytes(b"old-meta")
    (ast_dir / "wiki_plan.json").write_text('{"pages":[{"title":"Old"}]}')

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "worker.index.full.clone_or_fetch",
                new_callable=AsyncMock,
                return_value=("newsha", "main"),
            )
        )
        _enter_platform_patches(stack)
        stack.enter_context(
            patch(
                "worker.index.full.make_embedding_provider", return_value=mock_embedding
            )
        )
        stack.enter_context(
            patch(
                "worker.index.full.analyze_all_files",
                side_effect=RuntimeError("analysis failed"),
            )
        )

        from worker.jobs import run_full_index

        with pytest.raises(RuntimeError, match="analysis failed"):
            await run_full_index(
                ctx={},
                repo_id="restore-repo",
                job_id="restore-job",
                owner="testowner",
                name="simple-repo",
                clone_root=Path("tests/fixtures/simple-repo"),
            )

    try:
        async with get_session(db_path) as s:
            repo = await s.get(Repository, "restore-repo")
            assert repo.status == "ready"
            assert repo.description == "old description"
            assert repo.stars == 7
            assert repo.language == "Python"
            assert repo.last_commit == "oldsha"
            assert repo.indexed_at == indexed_at.replace(tzinfo=None)
            assert repo.wiki_structure == '{"pages":[{"title":"Old"}]}'

            from sqlalchemy import select

            result = await s.execute(
                select(WikiPage).where(WikiPage.repo_id == "restore-repo")
            )
            pages = result.scalars().all()
            assert len(pages) == 1
            assert pages[0].id == "old-page-id"
            assert pages[0].content == "old db content"

            job = await s.get(Job, "restore-job")
            assert job.status == "failed"

        assert (wiki_dir / "old-page.md").read_text() == "old file content"
        assert (repo_data / "faiss.index").read_bytes() == b"old-index"
        assert (repo_data / "faiss.meta.pkl").read_bytes() == b"old-meta"
        assert (ast_dir / "wiki_plan.json").read_text() == (
            '{"pages":[{"title":"Old"}]}'
        )
    finally:
        await dispose_db(db_path)
