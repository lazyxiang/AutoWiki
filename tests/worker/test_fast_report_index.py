import json
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
from worker.pipeline.dependency_graph import DependencyGraph
from worker.pipeline.fast_report_index import build_fast_report_index
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


def _enter_platform_patches(stack: ExitStack) -> None:
    stack.enter_context(
        patch("worker.jobs.get_platform_token", new=AsyncMock(return_value=None))
    )
    stack.enter_context(
        patch("worker.jobs.get_platform_by_name", return_value=_FAKE_PLATFORM)
    )


def test_build_fast_report_index_captures_required_fields(tmp_path: Path):
    worker_file = tmp_path / "worker" / "jobs.py"
    utils_file = tmp_path / "worker" / "utils.py"
    test_file = tmp_path / "tests" / "test_jobs.py"
    config_file = tmp_path / "pyproject.toml"

    worker_file.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    worker_file.write_text("def run_full_index():\n    pass\n")
    utils_file.write_text("def helper():\n    pass\n")
    test_file.write_text("from worker.jobs import run_full_index\n")
    config_file.write_text("[project]\nname = 'autowiki'\n")

    analysis = FileAnalysis(
        files={
            "worker/jobs.py": FileInfo(
                rel_path="worker/jobs.py",
                entities=[
                    {
                        "name": "run_full_index",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 2,
                        "signature": "run_full_index()",
                        "docstring": "Execute the full indexing pipeline.",
                    }
                ],
            ),
            "worker/utils.py": FileInfo(
                rel_path="worker/utils.py",
                entities=[
                    {
                        "name": "Helper",
                        "type": "class",
                        "start_line": 1,
                        "end_line": 2,
                    }
                ],
            ),
            "tests/test_jobs.py": FileInfo(rel_path="tests/test_jobs.py", entities=[]),
        }
    )
    dep_graph = DependencyGraph(
        edges={
            "worker/jobs.py": ["worker/utils.py"],
            "worker/utils.py": [],
            "tests/test_jobs.py": ["worker/jobs.py"],
        },
        external_deps={
            "worker/jobs.py": ["fastapi"],
            "tests/test_jobs.py": ["pytest"],
        },
    )

    index = build_fast_report_index(
        root=tmp_path,
        files=[worker_file, utils_file, test_file, config_file],
        file_analysis=analysis,
        dep_graph=dep_graph,
        readme="# AutoWiki\n## Architecture\n### Pipeline\n",
    )

    assert index["top_level_entries"] == [
        "pyproject.toml",
        "tests",
        "worker",
    ]
    assert index["readme_headings"] == ["AutoWiki", "Architecture", "Pipeline"]

    job_file = index["files"]["worker/jobs.py"]
    assert job_file["path"] == "worker/jobs.py"
    assert job_file["imports"] == ["worker/utils.py"]
    assert job_file["imported_by"] == ["tests/test_jobs.py"]
    assert job_file["external_deps"] == ["fastapi"]
    assert job_file["is_test"] is False
    assert job_file["is_config"] is False
    assert set(job_file["tokens"]) >= {
        "full",
        "index",
        "jobs",
        "py",
        "run",
        "worker",
    }
    assert job_file["entities"] == [
        {
            "name": "run_full_index",
            "type": "function",
            "start_line": 1,
            "end_line": 2,
            "signature": "run_full_index()",
            "docstring": "Execute the full indexing pipeline.",
            "symbol_path": "worker.jobs.run_full_index",
        }
    ]

    test_entry = index["files"]["tests/test_jobs.py"]
    assert test_entry["imports"] == ["worker/jobs.py"]
    assert test_entry["imported_by"] == []
    assert test_entry["external_deps"] == ["pytest"]
    assert test_entry["is_test"] is True
    assert test_entry["is_config"] is False

    config_entry = index["files"]["pyproject.toml"]
    assert config_entry["entities"] == []
    assert config_entry["imports"] == []
    assert config_entry["imported_by"] == []
    assert config_entry["external_deps"] == []
    assert config_entry["is_test"] is False
    assert config_entry["is_config"] is True


def test_build_fast_report_index_normalizes_mixed_path_separators(tmp_path: Path):
    worker_file = tmp_path / "worker" / "jobs.py"
    worker_file.parent.mkdir(parents=True)
    worker_file.write_text("def run_full_index():\n    pass\n")

    analysis = FileAnalysis(
        files={
            "worker\\jobs.py": FileInfo(
                rel_path="worker\\jobs.py",
                entities=[
                    {
                        "name": "run_full_index",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 2,
                    }
                ],
            )
        }
    )
    dep_graph = DependencyGraph(
        edges={"tests/test_jobs.py": ["worker\\jobs.py"], "worker\\jobs.py": []},
        external_deps={"worker\\jobs.py": ["fastapi"]},
    )

    index = build_fast_report_index(
        root=tmp_path,
        files=[worker_file],
        file_analysis=analysis,
        dep_graph=dep_graph,
        readme="# AutoWiki\n",
    )

    assert sorted(index["files"]) == ["worker/jobs.py"]
    job_file = index["files"]["worker/jobs.py"]
    assert job_file["imports"] == []
    assert job_file["imported_by"] == ["tests/test_jobs.py"]
    assert job_file["external_deps"] == ["fastapi"]
    assert "worker" in job_file["tokens"]
    assert "jobs" in job_file["tokens"]
    assert job_file["entities"] == [
        {
            "name": "run_full_index",
            "type": "function",
            "start_line": 1,
            "end_line": 2,
            "symbol_path": "worker.jobs.run_full_index",
        }
    ]


async def test_run_full_index_persists_fast_report_index(
    tmp_path, mock_llm, mock_fast_llm, mock_embedding
):
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Job, Repository
    from tests.conftest import FIXTURE_REPO
    from worker.jobs import run_full_index

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    mock_embedding.dimension = 1536
    repo_id = "fast_report_full_repo"
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

    with ExitStack() as stack:
        mock_cfg = stack.enter_context(patch("worker.jobs.get_config"))
        stack.enter_context(
            patch(
                "worker.jobs.clone_or_fetch",
                new_callable=AsyncMock,
                return_value=("abc123", "main"),
            )
        )
        _enter_platform_patches(stack)
        stack.enter_context(
            patch("worker.jobs.make_llm_provider", return_value=mock_llm)
        )
        stack.enter_context(
            patch("worker.jobs.make_fast_llm_provider", return_value=mock_fast_llm)
        )
        stack.enter_context(
            patch("worker.jobs.make_embedding_provider", return_value=mock_embedding)
        )
        cfg = mock_cfg.return_value
        cfg.database_path = tmp_path / "test.db"
        cfg.data_dir = tmp_path

        await run_full_index(
            {},
            repo_id=repo_id,
            job_id=job_id,
            owner="o",
            name="r",
            clone_root=FIXTURE_REPO,
        )

    try:
        fast_report_index_path = (
            tmp_path / "repos" / repo_id / "ast" / "fast_report_index.json"
        )
        assert fast_report_index_path.exists()
        index_data = json.loads(fast_report_index_path.read_text())
        assert "top_level_entries" in index_data
        assert "readme_headings" in index_data
        assert "files" in index_data
        assert "main.py" in index_data["files"]
        main_file = index_data["files"]["main.py"]
        assert isinstance(main_file["tokens"], list)
        assert isinstance(main_file["imports"], list)
        assert isinstance(main_file["imported_by"], list)
        assert isinstance(main_file["external_deps"], list)
        assert isinstance(main_file["entities"], list)
        assert "is_test" in main_file
        assert "is_config" in main_file
    finally:
        await dispose_db(db_path)


async def test_run_refresh_index_persists_fast_report_index(
    tmp_path, mock_llm, mock_fast_llm, mock_embedding
):
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Job, Repository, WikiPage
    from tests.conftest import FIXTURE_REPO
    from worker.jobs import run_refresh_index

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    mock_embedding.dimension = 1536
    repo_id = "fast_report_refresh_repo"
    job_id = str(uuid.uuid4())

    async with get_session(db_path) as s:
        s.add(
            Repository(
                id=repo_id,
                owner="o",
                name="r",
                status="ready",
                last_commit="old123",
            )
        )
        s.add(
            Job(id=job_id, repo_id=repo_id, type="refresh", status="queued", progress=0)
        )
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

    with ExitStack() as stack:
        mock_cfg = stack.enter_context(patch("worker.jobs.get_config"))
        stack.enter_context(
            patch(
                "worker.jobs.clone_or_fetch",
                new_callable=AsyncMock,
                return_value=("new456", "main"),
            )
        )
        stack.enter_context(
            patch(
                "worker.jobs.get_changed_files",
                new_callable=AsyncMock,
                return_value=["main.py"],
            )
        )
        _enter_platform_patches(stack)
        stack.enter_context(
            patch("worker.jobs.make_llm_provider", return_value=mock_llm)
        )
        stack.enter_context(
            patch("worker.jobs.make_fast_llm_provider", return_value=mock_fast_llm)
        )
        stack.enter_context(
            patch("worker.jobs.make_embedding_provider", return_value=mock_embedding)
        )
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

    try:
        fast_report_index_path = (
            tmp_path / "repos" / repo_id / "ast" / "fast_report_index.json"
        )
        assert fast_report_index_path.exists()
        index_data = json.loads(fast_report_index_path.read_text())
        assert "main.py" in index_data["files"]
        main_file = index_data["files"]["main.py"]
        assert isinstance(main_file["tokens"], list)
        assert isinstance(main_file["imports"], list)
        assert isinstance(main_file["imported_by"], list)
        assert isinstance(main_file["external_deps"], list)
        assert isinstance(main_file["entities"], list)
        assert main_file["is_test"] is False
        assert "is_config" in main_file
    finally:
        await dispose_db(db_path)
