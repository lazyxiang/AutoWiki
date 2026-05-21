import json
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from shared.database import dispose_db, get_session, init_db
from shared.models import Job, Repository
from tests.conftest import FIXTURE_REPO
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


def _enter_full_index_patches(
    stack: ExitStack,
    *,
    tmp_path: Path,
    mock_llm,
    mock_fast_llm,
) -> None:
    mock_cfg = stack.enter_context(patch("worker.index.full.get_config"))
    stack.enter_context(
        patch(
            "worker.index.full.clone_or_fetch",
            new_callable=AsyncMock,
            return_value=("abc123", "main"),
        )
    )
    stack.enter_context(
        patch("worker.index.full.get_platform_token", new=AsyncMock(return_value=None))
    )
    stack.enter_context(
        patch("worker.index.full.get_platform_by_name", return_value=_FAKE_PLATFORM)
    )
    stack.enter_context(
        patch("worker.index.full.make_llm_provider", return_value=mock_llm)
    )
    stack.enter_context(
        patch("worker.index.full.make_fast_llm_provider", return_value=mock_fast_llm)
    )
    cfg = mock_cfg.return_value
    cfg.database_path = tmp_path / "test.db"
    cfg.data_dir = tmp_path


async def _run_full_index(
    tmp_path: Path,
    *,
    mock_llm,
    mock_fast_llm,
) -> tuple[str, Path]:
    from worker.jobs import run_full_index

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    repo_id = f"repo_{uuid.uuid4().hex}"
    job_id = str(uuid.uuid4())

    async with get_session(db_path) as session:
        session.add(Repository(id=repo_id, owner="o", name="r", status="pending"))
        session.add(
            Job(
                id=job_id,
                repo_id=repo_id,
                type="full_index",
                status="queued",
                progress=0,
            )
        )
        await session.commit()

    with ExitStack() as stack:
        _enter_full_index_patches(
            stack,
            tmp_path=tmp_path,
            mock_llm=mock_llm,
            mock_fast_llm=mock_fast_llm,
        )
        await run_full_index(
            {},
            repo_id=repo_id,
            job_id=job_id,
            owner="o",
            name="r",
            clone_root=FIXTURE_REPO,
        )

    return db_path, tmp_path / "repos" / repo_id


async def test_full_index_ast_dir_contains_only_repo_index_and_wiki_plan(
    tmp_path, mock_llm, mock_fast_llm, monkeypatch
):
    monkeypatch.delenv("AUTOWIKI_DEBUG_DUMP_PROMPTS", raising=False)
    db_path, repo_data_dir = await _run_full_index(
        tmp_path,
        mock_llm=mock_llm,
        mock_fast_llm=mock_fast_llm,
    )

    try:
        ast_dir = repo_data_dir / "ast"
        ast_files = {path.name for path in ast_dir.iterdir()}

        assert ast_files == {"repo_index.json", "wiki_plan.json"}
        assert json.loads((ast_dir / "repo_index.json").read_text())["files"]
        assert json.loads((ast_dir / "wiki_plan.json").read_text())["pages"]
        assert not (ast_dir / "file_analysis_summary.txt").exists()
        assert not (ast_dir / "fast_report_index.json").exists()
        assert not (repo_data_dir / "faiss.index").exists()
        assert not (repo_data_dir / "faiss.meta.pkl").exists()
        assert not (repo_data_dir / "logs" / "phase1_prompt.txt").exists()
    finally:
        await dispose_db(db_path)


async def test_full_index_debug_prompt_dump_writes_actual_phase1_prompt(
    tmp_path, mock_llm, mock_fast_llm, monkeypatch
):
    monkeypatch.setenv("AUTOWIKI_DEBUG_DUMP_PROMPTS", "1")
    db_path, repo_data_dir = await _run_full_index(
        tmp_path,
        mock_llm=mock_llm,
        mock_fast_llm=mock_fast_llm,
    )

    try:
        prompt_path = repo_data_dir / "logs" / "phase1_prompt.txt"
        prompt = prompt_path.read_text(encoding="utf-8")

        assert "Repository: r" in prompt
        assert "File summaries:" in prompt
        assert "wiki" in prompt.lower()
    finally:
        await dispose_db(db_path)
