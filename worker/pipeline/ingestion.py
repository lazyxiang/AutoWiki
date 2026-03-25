from __future__ import annotations

import hashlib
from pathlib import Path

import pathspec

# Extensions considered source code (non-exhaustive, practical set)
SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".java",
    ".go",
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".hpp",
    ".cs",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".r",
    ".sh",
    ".bash",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".md",
    ".rst",
    ".txt",
    ".sql",
    ".graphql",
    ".proto",
}

EXCLUDED_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".pytest_cache",
    "venv",
    ".venv",
    "env",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    "vendor",
    "third_party",
    ".gradle",
    "coverage",
    ".coverage",
    "htmlcov",
}


def parse_github_url(url: str) -> tuple[str, str]:
    """Parse 'github.com/owner/repo' or full URL into (owner, name)."""
    url = url.replace("https://", "").replace("http://", "").rstrip("/")
    parts = url.split("/")
    # Find 'github.com' and take the next two parts
    try:
        idx = next(i for i, p in enumerate(parts) if p.lower() == "github.com")
        return parts[idx + 1], parts[idx + 2].removesuffix(".git")
    except (StopIteration, IndexError):
        raise ValueError(f"Cannot parse GitHub URL: {url}")


def get_repo_hash(platform: str, owner: str, name: str) -> str:
    key = f"{platform}:{owner}/{name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def filter_files(
    root: Path,
    max_file_bytes: int = 1024 * 1024,  # 1MB per file
    ignore_file: Path | None = None,
) -> list[Path]:
    """Return all indexable source files under root.

    If ignore_file exists and is a valid .gitignore-style file, patterns in it
    are applied to exclude additional paths.
    """
    spec: pathspec.PathSpec | None = None
    if ignore_file is not None and ignore_file.is_file():
        patterns = ignore_file.read_text(encoding="utf-8").splitlines()
        spec = pathspec.PathSpec.from_lines("gitignore", patterns)

    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        # Skip excluded directories
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        # Skip non-source extensions
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        # Skip oversized files
        if path.stat().st_size > max_file_bytes:
            continue
        # Apply .autowikiignore patterns
        if spec is not None and spec.match_file(str(rel)):
            continue
        results.append(path)
    return sorted(results)


def extract_readme(root: Path, max_chars: int = 3000) -> str | None:
    """Extract README content from the repository root."""
    for name in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
        p = root / name
        if p.exists() and p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")[:max_chars]
            except OSError:
                continue
    return None


async def clone_or_fetch(clone_dir: Path, owner: str, name: str) -> str:
    """Clone or fetch a GitHub repo. Returns HEAD commit SHA.

    Runs blocking gitpython I/O in a thread executor to avoid stalling the event loop.
    """
    import asyncio

    import git

    def _do_clone_or_fetch() -> str:
        url = f"https://github.com/{owner}/{name}.git"
        if (clone_dir / ".git").exists():
            repo = git.Repo(clone_dir)
            repo.remotes.origin.fetch()
            repo.head.reset("FETCH_HEAD", index=True, working_tree=True)
        else:
            clone_dir.mkdir(parents=True, exist_ok=True)
            repo = git.Repo.clone_from(url, clone_dir, depth=1)
        return repo.head.commit.hexsha

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_clone_or_fetch)


def get_changed_files(clone_dir: Path, old_sha: str, new_sha: str) -> list[str]:
    """Return list of file paths changed between two git SHAs.

    Raises git.exc.GitCommandError if either SHA is not reachable in the repo
    history (e.g. shallow clones missing the old commit).
    """
    import git
    repo = git.Repo(clone_dir)
    diff_output = repo.git.diff("--name-only", old_sha, new_sha)
    if not diff_output:
        return []
    return [line for line in diff_output.split("\n") if line.strip()]


def get_affected_modules(changed_files: list[str], module_tree: list[dict]) -> set[str]:
    """Return the set of module paths (from module_tree) touched by changed_files."""
    module_paths = {m["path"] for m in module_tree}
    affected: set[str] = set()
    for f in changed_files:
        parts = Path(f).parts
        # Root-level files (len==1) map to "." — they don't belong to any module
        module = parts[0] if len(parts) > 1 else "."
        if module in module_paths:
            affected.add(module)
    return affected
