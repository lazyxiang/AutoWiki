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
