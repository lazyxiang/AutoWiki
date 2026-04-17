import pytest

from worker.pipeline.ingestion import (
    extract_readme,
    filter_files,
    get_repo_hash,
    parse_github_url,
)


def test_parse_github_url():
    owner, name = parse_github_url("https://github.com/psf/requests")
    assert owner == "psf"
    assert name == "requests"


def test_parse_github_url_without_scheme():
    owner, name = parse_github_url("github.com/psf/requests")
    assert owner == "psf"
    assert name == "requests"


def test_get_repo_hash_is_deterministic():
    h1 = get_repo_hash("github", "psf", "requests")
    h2 = get_repo_hash("github", "psf", "requests")
    assert h1 == h2
    assert len(h1) == 16  # truncated sha256


def test_filter_files_excludes_binaries(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("// lib")
    files = filter_files(tmp_path)
    paths = [f.name for f in files]
    assert "main.py" in paths
    assert "image.png" not in paths
    assert "lib.js" not in paths


def test_filter_files_respects_size_limit(tmp_path):
    small = tmp_path / "small.py"
    large = tmp_path / "large.py"
    small.write_text("x = 1")
    large.write_bytes(b"x" * (2 * 1024 * 1024))  # 2MB > 1MB limit
    files = filter_files(tmp_path, max_file_bytes=1024 * 1024)
    assert small in files
    assert large not in files


def test_parse_github_url_invalid_raises():
    with pytest.raises(ValueError, match="Cannot parse GitHub URL"):
        parse_github_url("not-a-url")


def test_parse_github_url_rejects_non_github():
    with pytest.raises(ValueError):
        parse_github_url("https://gitlab.com/owner/repo")


def test_filter_files_uses_relative_parts(tmp_path):
    """Ensure paths outside root with excluded dir names don't falsely skip files."""
    # Simulate clone dir under a path containing a dir named 'build'
    build_dir = tmp_path / "build" / "clone"
    build_dir.mkdir(parents=True)
    (build_dir / "main.py").write_text("x = 1")
    files = filter_files(build_dir)
    # main.py should be found even though 'build' is an
    # excluded dir name in the parent path
    assert any(f.name == "main.py" for f in files)


def test_filter_files_respects_autowikiignore(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "test_main.py").write_text("# test")
    (tmp_path / ".autowikiignore").write_text("test_*.py\n")
    files = filter_files(tmp_path, ignore_file=tmp_path / ".autowikiignore")
    names = [f.name for f in files]
    assert "main.py" in names
    assert "test_main.py" not in names


def test_filter_files_ignores_missing_autowikiignore(tmp_path):
    (tmp_path / "main.py").write_text("x = 1")
    # No .autowikiignore — should not raise, should return main.py
    files = filter_files(tmp_path, ignore_file=tmp_path / ".autowikiignore")
    assert any(f.name == "main.py" for f in files)


async def test_get_changed_files_returns_diff(tmp_path):
    import git

    from worker.pipeline.ingestion import get_changed_files

    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "test").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()
    (tmp_path / "a.py").write_text("x = 1")
    repo.index.add(["a.py"])
    c1 = repo.index.commit("first")
    (tmp_path / "b.py").write_text("y = 2")
    repo.index.add(["b.py"])
    c2 = repo.index.commit("second")
    changed = await get_changed_files(tmp_path, c1.hexsha, c2.hexsha)
    assert "b.py" in changed


def test_get_affected_pages():
    from worker.pipeline.ingestion import get_affected_pages
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="API", purpose="API endpoints.", files=["api/main.py"]),
            WikiPageSpec(
                title="Worker", purpose="Background jobs.", files=["worker/main.py"]
            ),
        ]
    )
    affected = get_affected_pages(["api/main.py", "README.md"], plan)
    assert affected.primary == {"API"}
    assert affected.secondary == set()
    empty = get_affected_pages([], plan)
    assert empty.primary == set()
    assert empty.secondary == set()


def test_get_affected_pages_distinguishes_primary_from_secondary():
    from worker.pipeline.ingestion import AffectedPages, get_affected_pages
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Core",
                purpose="p",
                files=["core/a.py"],
                secondary_files=[],
            ),
            WikiPageSpec(
                title="API",
                purpose="p",
                files=["api/r.py"],
                secondary_files=["core/a.py"],
            ),
        ]
    )
    result = get_affected_pages(["core/a.py"], plan)
    assert isinstance(result, AffectedPages)
    assert result.primary == {"Core"}
    assert result.secondary == {"API"}


def test_get_affected_pages_primary_wins_over_secondary():
    from worker.pipeline.ingestion import get_affected_pages
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(
        pages=[
            WikiPageSpec(title="P", purpose="p", files=["x.py"], secondary_files=[]),
            WikiPageSpec(title="Q", purpose="p", files=[], secondary_files=["x.py"]),
        ]
    )
    result = get_affected_pages(["x.py"], plan)
    assert result.primary == {"P"}
    assert result.secondary == {"Q"}

    # Also check that if a single page has a file in both sets, primary wins
    plan_overlap = WikiPlan(
        pages=[
            WikiPageSpec(
                title="P", purpose="p", files=["x.py"], secondary_files=["x.py"]
            ),
        ]
    )
    result_overlap = get_affected_pages(["x.py"], plan_overlap)
    assert result_overlap.primary == {"P"}
    assert result_overlap.secondary == set()
    assert result_overlap.primary.isdisjoint(result_overlap.secondary)


def test_get_affected_pages_legacy_plans_without_secondary_still_work():
    from worker.pipeline.ingestion import get_affected_pages
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(pages=[WikiPageSpec(title="A", purpose="p", files=["f.py"])])
    result = get_affected_pages(["f.py"], plan)
    assert result.primary == {"A"}
    assert result.secondary == set()


def test_extract_readme_finds_markdown(tmp_path):
    (tmp_path / "README.md").write_text("# My Project\nSome description.")
    result = extract_readme(tmp_path)
    assert result is not None
    assert "My Project" in result


def test_extract_readme_truncates(tmp_path):
    (tmp_path / "README.md").write_text("x" * 5000)
    result = extract_readme(tmp_path, max_chars=100)
    assert result is not None
    assert len(result) == 100


def test_extract_readme_returns_none_when_missing(tmp_path):
    result = extract_readme(tmp_path)
    assert result is None


def test_extract_readme_case_insensitive(tmp_path):
    (tmp_path / "readme.md").write_text("# Lower case readme")
    result = extract_readme(tmp_path)
    assert result is not None
    assert "Lower case" in result
