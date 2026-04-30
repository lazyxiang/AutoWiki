import json
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from worker.pipeline.ast_analysis import FileAnalysis, FileInfo, analyze_all_files
from worker.pipeline.dependency_graph import DependencyGraph, build_dependency_graph
from worker.pipeline.fast_report_index import (
    INDEX_VERSION,
    _build_directory_tree,
    _build_directory_tree_with_degradation,
    _compute_hub_modules,
    _extract_readme_sections,
    build_fast_report_index,
)
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


def _enter_platform_patches(
    stack: ExitStack, module: str = "worker.index.full"
) -> None:
    stack.enter_context(
        patch(f"{module}.get_platform_token", new=AsyncMock(return_value=None))
    )
    stack.enter_context(
        patch(f"{module}.get_platform_by_name", return_value=_FAKE_PLATFORM)
    )


def _build_index_for_dir(root: Path, *, make_empty: bool = False) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    if make_empty:
        files: list[Path] = []
    else:
        files = sorted(
            p
            for p in root.rglob("*")
            if p.is_file() and p.suffix in {".py", ".js", ".ts", ".go"}
        )
    file_analysis = analyze_all_files(root, files)
    dep_graph = build_dependency_graph(files, root)
    readme_file = root / "README.md"
    readme = readme_file.read_text() if readme_file.exists() else ""
    return build_fast_report_index(
        root=root,
        files=files,
        file_analysis=file_analysis,
        dep_graph=dep_graph,
        readme=readme,
    )


def test_build_directory_tree_nested_indent_format():
    rel_paths = [
        "api/main.py",
        "api/routes/repos.py",
        "worker/fast_report.py",
        "README.md",
    ]
    tree = _build_directory_tree(rel_paths)
    assert tree == (
        "README.md\n"
        "api/\n"
        "  main.py\n"
        "  routes/\n"
        "    repos.py\n"
        "worker/\n"
        "  fast_report.py\n"
    )


def test_build_directory_tree_excludes_known_dirs_and_globs():
    rel_paths = [
        "src/main.py",
        ".git/HEAD",
        "node_modules/foo/index.js",
        "dist/bundle.js",
        "build/out.o",
        "__pycache__/main.cpython-311.pyc",
        ".venv/lib/python.py",
        "tests/sample.min.js",
        "package-lock.json",
        "src/main.pyc",
    ]
    tree = _build_directory_tree(rel_paths)
    assert "src/" in tree
    assert "main.py" in tree
    assert ".git" not in tree
    assert "node_modules" not in tree
    assert "dist" not in tree
    assert "build" not in tree
    assert "__pycache__" not in tree
    assert ".venv" not in tree
    assert "min.js" not in tree
    assert "package-lock.json" not in tree
    assert "main.pyc" not in tree


def test_directory_tree_falls_back_to_depth_three_over_cap():
    deep_paths = [f"src/a/b/c/d/file_{i}.py" for i in range(20000)] + [
        "src/a/README.md"
    ]
    hub_paths = {"src/a/b/c/d/file_0.py"}
    tree = _build_directory_tree_with_degradation(deep_paths, hub_paths=hub_paths)
    assert "file_1.py" not in tree
    assert "file_0.py" in tree
    assert "src/" in tree and "  a/" in tree


def test_hub_modules_ranks_by_in_degree_and_truncates_purpose():
    files = {
        "shared/types.py": {
            "path": "shared/types.py",
            "imported_by": ["a.py", "b.py"],
            "module_docstring": "Types module. Internal helpers.",
        },
        "shared/util.py": {
            "path": "shared/util.py",
            "imported_by": ["a.py"],
            "module_docstring": None,
        },
        "main.py": {
            "path": "main.py",
            "imported_by": [],
            "module_docstring": "Entrypoint",
        },
    }
    hubs = _compute_hub_modules(files)
    assert hubs[0]["path"] == "shared/types.py"
    assert hubs[0]["in_degree"] == 2
    assert hubs[0]["purpose"] == "Types module."
    assert all(h["in_degree"] >= 2 for h in hubs)


def test_readme_sections_caps_per_section_at_800_chars():
    big = "x" * 5000
    readme = f"# Top\nintro\n\n## Architecture\n{big}\n\n## Deployment\nshort body"
    sections = _extract_readme_sections(readme)
    headings = [s["heading"] for s in sections]
    assert headings == ["Top", "Architecture", "Deployment"]
    arch = next(s for s in sections if s["heading"] == "Architecture")
    assert len(arch["body"]) == 800


def test_readme_sections_cumulative_cap_drops_later():
    body = "y" * 800
    chunks = [f"## H{i}\n{body}" for i in range(60)]
    readme = "\n\n".join(chunks)
    sections = _extract_readme_sections(readme)
    assert len(sections) < 60


def test_index_entry_carries_module_docstring(tmp_path: Path):
    (tmp_path / "mod.py").write_text('"""Hello module."""\n\ndef f():\n    pass\n')
    index = _build_index_for_dir(tmp_path)
    assert index["files"]["mod.py"]["module_docstring"] == "Hello module."


def test_entity_leading_comment_attached(tmp_path: Path):
    (tmp_path / "mod.py").write_text(
        "# Single-pass AST analyzer.\n"
        "# Companion to tree-sitter parsing.\n"
        "def analyze():\n"
        "    pass\n"
    )
    index = _build_index_for_dir(tmp_path)
    entity = next(
        e for e in index["files"]["mod.py"]["entities"] if e["name"] == "analyze"
    )
    assert entity.get("leading_comment", "").startswith("Single-pass")


def test_blank_line_between_comment_and_entity_skips_leading_comment(tmp_path: Path):
    (tmp_path / "mod.py").write_text("# Unrelated note\n\ndef fn():\n    pass\n")
    index = _build_index_for_dir(tmp_path)
    entity = next(e for e in index["files"]["mod.py"]["entities"] if e["name"] == "fn")
    assert "leading_comment" not in entity or entity["leading_comment"] in (None, "")


def test_call_sites_collected_for_python(tmp_path: Path):
    (tmp_path / "a.py").write_text(
        "def caller():\n    helper()\n\ndef helper():\n    pass\n"
    )
    index = _build_index_for_dir(tmp_path)
    entry = index["files"]["a.py"]
    sites = entry["call_sites"]
    assert any(s["callee_name"] == "helper" and s["line"] == 2 for s in sites)


def test_exception_touchpoints_record_message_when_literal(tmp_path: Path):
    (tmp_path / "b.py").write_text(
        "def fn():\n"
        "    try:\n"
        "        x = 1\n"
        "    except ValueError:\n"
        "        raise ValueError('boom')\n"
    )
    index = _build_index_for_dir(tmp_path)
    touchpoints = index["files"]["b.py"]["exception_touchpoints"]
    kinds = {t["kind"] for t in touchpoints}
    assert {"try", "except", "raise"}.issubset(kinds)
    raised = next(t for t in touchpoints if t["kind"] == "raise")
    assert raised["message"] == "boom"


def test_config_touchpoints_capture_env_keys(tmp_path: Path):
    (tmp_path / "c.py").write_text(
        "import os\n\nKEY = os.getenv('AUTOWIKI_LLM_PROVIDER')\n"
    )
    index = _build_index_for_dir(tmp_path)
    cps = index["files"]["c.py"]["config_touchpoints"]
    assert any(
        c["config_key"] == "AUTOWIKI_LLM_PROVIDER" and c["kind"] == "read" for c in cps
    )


def test_index_version_is_2_and_no_top_level_entries(tmp_path: Path):
    index = _build_index_for_dir(tmp_path / "empty_repo", make_empty=True)
    assert index["index_version"] == INDEX_VERSION == 2
    assert "top_level_entries" not in index


def test_index_carries_directory_tree_hub_modules_and_readme_sections(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Title\nintro\n\n## Architecture\nbody")
    (repo / "lib.py").write_text('"""Lib."""\n\ndef f():\n    pass\n')
    (repo / "a.py").write_text("import lib\n")
    (repo / "b.py").write_text("import lib\n")
    index = _build_index_for_dir(repo)
    assert "lib.py" in index["directory_tree"]
    assert any(h["path"] == "lib.py" for h in index["hub_modules"])
    assert index["readme_sections"][0]["heading"] == "Title"


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

    assert index["index_version"] == 2
    assert "top_level_entries" not in index
    assert index["directory_tree"] == (
        "pyproject.toml\ntests/\n  test_jobs.py\nworker/\n  jobs.py\n  utils.py\n"
    )
    assert index["hub_modules"] == []
    assert index["readme_headings"] == ["AutoWiki", "Architecture", "Pipeline"]
    assert index["readme_sections"] == [
        {"heading": "AutoWiki", "body": ""},
        {"heading": "Architecture", "body": ""},
        {"heading": "Pipeline", "body": ""},
    ]

    job_file = index["files"]["worker/jobs.py"]
    assert job_file["path"] == "worker/jobs.py"
    assert job_file["imports"] == ["worker/utils.py"]
    assert job_file["imported_by"] == ["tests/test_jobs.py"]
    assert job_file["external_deps"] == ["fastapi"]
    assert job_file["is_test"] is False
    assert job_file["is_config"] is False
    assert job_file["module_docstring"] is None
    assert job_file["call_sites"] == []
    assert job_file["exception_touchpoints"] == []
    assert job_file["config_touchpoints"] == []
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
    assert config_entry["module_docstring"] is None
    assert config_entry["call_sites"] == []
    assert config_entry["exception_touchpoints"] == []
    assert config_entry["config_touchpoints"] == []


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
        assert index_data["index_version"] == 2
        assert "top_level_entries" not in index_data
        assert "directory_tree" in index_data
        assert "hub_modules" in index_data
        assert "readme_headings" in index_data
        assert "readme_sections" in index_data
        assert "files" in index_data
        assert "main.py" in index_data["files"]
        main_file = index_data["files"]["main.py"]
        assert isinstance(main_file["tokens"], list)
        assert isinstance(main_file["imports"], list)
        assert isinstance(main_file["imported_by"], list)
        assert isinstance(main_file["external_deps"], list)
        assert isinstance(main_file["entities"], list)
        assert "module_docstring" in main_file
        assert isinstance(main_file["call_sites"], list)
        assert isinstance(main_file["exception_touchpoints"], list)
        assert isinstance(main_file["config_touchpoints"], list)
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
        mock_cfg = stack.enter_context(patch("worker.index.refresh.get_config"))
        stack.enter_context(
            patch(
                "worker.index.refresh.clone_or_fetch",
                new_callable=AsyncMock,
                return_value=("new456", "main"),
            )
        )
        stack.enter_context(
            patch(
                "worker.index.refresh.get_changed_files",
                new_callable=AsyncMock,
                return_value=["main.py"],
            )
        )
        _enter_platform_patches(stack, module="worker.index.refresh")
        stack.enter_context(
            patch("worker.index.refresh.make_llm_provider", return_value=mock_llm)
        )
        stack.enter_context(
            patch(
                "worker.index.refresh.make_fast_llm_provider",
                return_value=mock_fast_llm,
            )
        )
        stack.enter_context(
            patch(
                "worker.index.refresh.make_embedding_provider",
                return_value=mock_embedding,
            )
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
