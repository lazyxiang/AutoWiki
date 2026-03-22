from __future__ import annotations
import hashlib
from pathlib import Path

# Extensions considered source code (non-exhaustive, practical set)
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
    ".c", ".h", ".cpp", ".cc", ".hpp", ".cs", ".rs",
    ".rb", ".php", ".swift", ".kt", ".scala", ".r",
    ".sh", ".bash", ".yaml", ".yml", ".toml", ".json",
    ".md", ".rst", ".txt", ".sql", ".graphql", ".proto",
}

EXCLUDED_DIRS = {
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    "venv", ".venv", "env", "dist", "build", "target",
    ".next", ".nuxt", "vendor", "third_party", ".gradle",
    "coverage", ".coverage", "htmlcov",
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
) -> list[Path]:
    """Return all indexable source files under root."""
    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # Skip excluded directories
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        # Skip non-source extensions
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        # Skip oversized files
        if path.stat().st_size > max_file_bytes:
            continue
        results.append(path)
    return sorted(results)


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
