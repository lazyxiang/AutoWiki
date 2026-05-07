import json
import threading
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_single_file_analysis(path: str = "main.py"):
    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo

    return FileAnalysis(
        files={
            path: FileInfo(
                rel_path=path,
                entities=[],
                class_count=0,
                function_count=0,
                summary="",
            )
        }
    )


async def _seed_refresh_repo(
    tmp_path,
    *,
    repo_id: str,
    job_id: str,
    old_sha: str = "old123",
    write_markdown: bool = False,
):
    from shared.database import get_session, init_db
    from shared.models import Job, Repository, WikiPage

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    clone_root = tmp_path / "clone"
    clone_root.mkdir()

    async with get_session(db_path) as s:
        s.add(
            Repository(
                id=repo_id, owner="o", name="r", status="ready", last_commit=old_sha
            )
        )
        s.add(
            Job(id=job_id, repo_id=repo_id, type="refresh", status="queued", progress=0)
        )
        s.add(
            WikiPage(
                id="overview-row",
                repo_id=repo_id,
                slug="overview",
                title="Overview",
                content="old overview",
                page_order=0,
            )
        )
        await s.commit()

    repo_data = tmp_path / "repos" / repo_id
    ast_dir = repo_data / "ast"
    wiki_dir = repo_data / "wiki"
    ast_dir.mkdir(parents=True)
    if write_markdown:
        wiki_dir.mkdir()
        (wiki_dir / "overview.md").write_text("old overview file")

    (ast_dir / "wiki_plan.json").write_text(
        json.dumps(
            {
                "repo_notes": [{"content": ""}],
                "all_repo_files": ["main.py"],
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
    return db_path, clone_root, wiki_dir


async def _run_refresh_with_mocks(
    tmp_path,
    *,
    repo_id: str,
    job_id: str,
    clone_root,
    mock_llm,
    mock_fast_llm,
    mock_embedding,
    changed_files: list[str],
    generate_wiki_plan,
    generate_page_batch,
    readme: str = "",
    build_dependency_graph=None,
    build_repo_index=None,
):
    from worker.jobs import run_refresh_index
    from worker.pipeline.dependency_graph import DependencyGraph

    file_analysis = _make_single_file_analysis()
    mock_embedding.dimension = 1536
    dependency_graph_builder = build_dependency_graph or (
        lambda *_args, **_kwargs: DependencyGraph(edges={}, clusters=[["main.py"]])
    )
    repo_index_builder = build_repo_index or (lambda *_args, **_kwargs: {})
    with (
        patch("worker.index.refresh.get_config") as mock_cfg,
        patch(
            "worker.index.refresh.clone_or_fetch",
            new_callable=AsyncMock,
            return_value=("new456", "main"),
        ),
        patch(
            "worker.index.refresh.get_changed_files",
            new_callable=AsyncMock,
            return_value=changed_files,
        ),
        patch(
            "worker.index.refresh.filter_files", return_value=[clone_root / "main.py"]
        ),
        patch("worker.index.refresh.extract_readme", return_value=readme),
        patch("worker.index.refresh.load_user_steering", return_value=None),
        patch("worker.index.refresh.analyze_all_files", return_value=file_analysis),
        patch(
            "worker.index.refresh.build_dependency_graph",
            side_effect=dependency_graph_builder,
        ),
        patch("worker.index.refresh.build_repo_index", side_effect=repo_index_builder),
        patch("worker.index.refresh._make_faiss_store", return_value=MagicMock()),
        patch("worker.index.refresh.build_rag_index", new_callable=AsyncMock),
        patch("worker.index.refresh.make_llm_provider", return_value=mock_llm),
        patch(
            "worker.index.refresh.make_fast_llm_provider", return_value=mock_fast_llm
        ),
        patch(
            "worker.index.refresh.make_embedding_provider", return_value=mock_embedding
        ),
        patch("worker.index.refresh.generate_wiki_plan", new=generate_wiki_plan),
        patch("worker.index.refresh.generate_page_batch", new=generate_page_batch),
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
            clone_root=clone_root,
        )


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
        patch("worker.index.refresh.get_config") as mock_cfg,
        patch(
            "worker.index.refresh.clone_or_fetch",
            new_callable=AsyncMock,
            return_value=("abc123", "main"),
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


async def test_run_refresh_index_with_changes(
    tmp_path, mock_llm, mock_fast_llm, mock_embedding
):
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
        patch("worker.index.refresh.get_config") as mock_cfg,
        patch(
            "worker.index.refresh.clone_or_fetch",
            new_callable=AsyncMock,
            return_value=(new_sha, "main"),
        ),
        patch(
            "worker.index.refresh.get_changed_files",
            new_callable=AsyncMock,
            return_value=["main.py"],
        ),
        patch("worker.index.refresh.make_llm_provider", return_value=mock_llm),
        patch(
            "worker.index.refresh.make_fast_llm_provider", return_value=mock_fast_llm
        ),
        patch(
            "worker.index.refresh.make_embedding_provider", return_value=mock_embedding
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

        # Affected page should have been regenerated (content replaced)
        from sqlalchemy import select as sa_select

        result = await s.execute(
            sa_select(WikiPage).where(
                WikiPage.repo_id == repo_id, WikiPage.slug == "overview"
            )
        )
        overview = result.scalar_one_or_none()
        assert overview is not None
        assert overview.content != "old content", (
            "page content should have been regenerated"
        )
    await dispose_db(db_path)


async def test_run_refresh_index_replans_all_files_for_readme_only_change(
    tmp_path, mock_llm, mock_fast_llm, mock_embedding
):
    """README-only changes force a global replan with the full source analysis."""
    from worker.pipeline.page.generator import PageResult
    from worker.pipeline.planner.wiki_planner import WikiPageSpec, WikiPlan

    repo_id = "readme_refresh_repo"
    job_id = str(uuid.uuid4())
    db_path, clone_root, _wiki_dir = await _seed_refresh_repo(
        tmp_path, repo_id=repo_id, job_id=job_id
    )
    captured: dict[str, object] = {}

    async def fake_generate_wiki_plan(affected_file_analysis, **kwargs):
        captured["files"] = set(affected_file_analysis.files)
        captured["existing_titles"] = kwargs["existing_titles"]
        return WikiPlan(
            pages=[
                WikiPageSpec(
                    title="Overview",
                    purpose="Updated from README.",
                    files=["main.py"],
                )
            ],
            all_repo_files=["main.py"],
        )

    async def fake_generate_page_batch(specs_with_children, *args, on_result, **kwargs):
        spec = specs_with_children[0][0]
        await on_result(
            PageResult(slug=spec.slug, title=spec.title, content="new content"), spec
        )
        return []

    await _run_refresh_with_mocks(
        tmp_path,
        repo_id=repo_id,
        job_id=job_id,
        clone_root=clone_root,
        mock_llm=mock_llm,
        mock_fast_llm=mock_fast_llm,
        mock_embedding=mock_embedding,
        changed_files=["README.md"],
        readme="# New README",
        generate_wiki_plan=fake_generate_wiki_plan,
        generate_page_batch=fake_generate_page_batch,
    )

    try:
        assert captured["files"] == {"main.py"}
        assert captured["existing_titles"] == set()
    finally:
        from shared.database import dispose_db

        await dispose_db(db_path)


async def test_run_refresh_index_builds_dependency_graph_and_repo_index_in_executor(
    tmp_path, mock_llm, mock_fast_llm, mock_embedding
):
    from shared.database import dispose_db
    from worker.pipeline.dependency_graph import DependencyGraph
    from worker.pipeline.page.generator import PageResult
    from worker.pipeline.planner.wiki_planner import WikiPageSpec, WikiPlan

    repo_id = "executor_refresh_repo"
    job_id = str(uuid.uuid4())
    db_path, clone_root, _wiki_dir = await _seed_refresh_repo(
        tmp_path, repo_id=repo_id, job_id=job_id
    )
    event_loop_thread = threading.get_ident()
    worker_threads: dict[str, int] = {}

    def fake_build_dependency_graph(*_args, **_kwargs):
        worker_threads["dependency_graph"] = threading.get_ident()
        return DependencyGraph(edges={}, clusters=[["main.py"]])

    def fake_build_repo_index(*_args, **_kwargs):
        worker_threads["repo_index"] = threading.get_ident()
        return {"index_version": 2, "files": {"main.py": {}}}

    async def fake_generate_wiki_plan(*_args, **_kwargs):
        return WikiPlan(
            pages=[
                WikiPageSpec(
                    title="Overview",
                    purpose="Updated source page.",
                    files=["main.py"],
                )
            ],
            all_repo_files=["main.py"],
        )

    async def fake_generate_page_batch(specs_with_children, *args, on_result, **kwargs):
        spec = specs_with_children[0][0]
        await on_result(
            PageResult(slug=spec.slug, title=spec.title, content="new content"), spec
        )
        return []

    try:
        await _run_refresh_with_mocks(
            tmp_path,
            repo_id=repo_id,
            job_id=job_id,
            clone_root=clone_root,
            mock_llm=mock_llm,
            mock_fast_llm=mock_fast_llm,
            mock_embedding=mock_embedding,
            changed_files=["main.py"],
            generate_wiki_plan=fake_generate_wiki_plan,
            generate_page_batch=fake_generate_page_batch,
            build_dependency_graph=fake_build_dependency_graph,
            build_repo_index=fake_build_repo_index,
        )

        assert worker_threads["dependency_graph"] != event_loop_thread
        assert worker_threads["repo_index"] != event_loop_thread
    finally:
        await dispose_db(db_path)


async def test_run_refresh_index_keeps_existing_pages_if_generation_fails(
    tmp_path, mock_llm, mock_fast_llm, mock_embedding
):
    """A refresh failure after a generated result must not delete live wiki rows."""
    from sqlalchemy import select as sa_select

    from shared.database import dispose_db, get_session
    from shared.models import WikiPage
    from worker.pipeline.page.generator import PageResult
    from worker.pipeline.planner.wiki_planner import WikiPageSpec, WikiPlan

    repo_id = "failed_refresh_repo"
    job_id = str(uuid.uuid4())
    db_path, clone_root, wiki_dir = await _seed_refresh_repo(
        tmp_path, repo_id=repo_id, job_id=job_id, write_markdown=True
    )

    async def fake_generate_wiki_plan(*args, **kwargs):
        return WikiPlan(
            pages=[
                WikiPageSpec(
                    title="Overview",
                    purpose="Updated source page.",
                    files=["main.py"],
                )
            ],
            all_repo_files=["main.py"],
        )

    async def failing_generate_page_batch(
        specs_with_children, *args, on_result, **kwargs
    ):
        spec = specs_with_children[0][0]
        await on_result(
            PageResult(slug=spec.slug, title=spec.title, content="new content"),
            spec,
        )
        raise RuntimeError("page generation failed")

    with pytest.raises(RuntimeError, match="page generation failed"):
        await _run_refresh_with_mocks(
            tmp_path,
            repo_id=repo_id,
            job_id=job_id,
            clone_root=clone_root,
            mock_llm=mock_llm,
            mock_fast_llm=mock_fast_llm,
            mock_embedding=mock_embedding,
            changed_files=["main.py"],
            generate_wiki_plan=fake_generate_wiki_plan,
            generate_page_batch=failing_generate_page_batch,
        )

    try:
        async with get_session(db_path) as s:
            result = await s.execute(
                sa_select(WikiPage).where(
                    WikiPage.repo_id == repo_id, WikiPage.slug == "overview"
                )
            )
            page = result.scalar_one()
            assert page.content == "old overview"
        assert (wiki_dir / "overview.md").read_text() == "old overview file"
    finally:
        await dispose_db(db_path)


def test_merge_refresh_plan_pages_preserves_old_order_and_appends_new_pages():
    from worker.index.refresh import _merge_refresh_plan_pages
    from worker.pipeline.planner.wiki_planner import WikiPageSpec

    old_pages = [
        WikiPageSpec(title="Overview", purpose="old", files=["main.py"]),
        WikiPageSpec(title="API", purpose="old", files=["api.py"]),
        WikiPageSpec(title="CLI", purpose="old", files=["cli.py"]),
    ]
    replacement_pages = [
        WikiPageSpec(title="API", purpose="new", files=["api.py"]),
        WikiPageSpec(title="Configuration", purpose="new", files=["config.py"]),
    ]

    merged = _merge_refresh_plan_pages(
        old_pages=old_pages,
        replacement_pages=replacement_pages,
        affected_titles={"API"},
    )

    assert [p.title for p in merged] == ["Overview", "API", "CLI", "Configuration"]
    assert merged[1].purpose == "new"
