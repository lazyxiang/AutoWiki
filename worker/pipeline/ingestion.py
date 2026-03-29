"""Stage 1 of the generation pipeline.

Covers repository cloning, file filtering, and diff detection.

This module handles everything that happens before analysis:
  - Parsing GitHub URLs into owner/name tuples
  - Hashing repo identifiers to stable storage keys
  - Walking the local clone to collect indexable source files
  - Extracting the README for later use in the wiki plan prompt
  - Cloning or fetching a GitHub repo via gitpython (in a thread executor)
  - Computing which files changed between two commits
  - Mapping changed files to wiki pages that must be regenerated
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import pathspec

if TYPE_CHECKING:
    from worker.pipeline.wiki_planner import WikiPlan

# Allowlist of file extensions treated as indexable source/documentation.
# Files with extensions not in this set are silently skipped by filter_files().
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

# Directory names that are always skipped to avoid indexing build artefacts,
# caches, vendored code, and VCS metadata that would pollute the wiki.
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
    """Parse a GitHub URL or bare path into an (owner, name) tuple.

    Accepts full HTTPS URLs as well as short-form ``github.com/owner/repo``
    strings.  The ``.git`` suffix is stripped if present.

    Args:
        url: A GitHub repository URL in any of these forms:
            ``https://github.com/owner/repo``,
            ``http://github.com/owner/repo``,
            ``github.com/owner/repo``, or
            ``github.com/owner/repo.git``.

    Returns:
        tuple[str, str]: A two-element tuple ``(owner, name)`` where *owner*
        is the GitHub organisation or user and *name* is the repository name
        without the ``.git`` suffix.

    Raises:
        ValueError: If ``url`` does not contain ``github.com`` followed by at
            least two path segments (owner and repo name).

    Example:
        >>> parse_github_url("https://github.com/anthropics/anthropic-sdk-python")
        ('anthropics', 'anthropic-sdk-python')
        >>> parse_github_url("github.com/owner/repo.git")
        ('owner', 'repo')
    """
    url = url.replace("https://", "").replace("http://", "").rstrip("/")
    parts = url.split("/")
    # Find 'github.com' and take the next two parts
    try:
        idx = next(i for i, p in enumerate(parts) if p.lower() == "github.com")
        return parts[idx + 1], parts[idx + 2].removesuffix(".git")
    except (StopIteration, IndexError):
        raise ValueError(f"Cannot parse GitHub URL: {url}")


def get_repo_hash(platform: str, owner: str, name: str) -> str:
    """Return a stable, short hash that uniquely identifies a repository.

    The hash is derived from a ``platform:owner/name`` key so that the same
    repository always maps to the same storage directory regardless of which
    URL form was used to request it.

    Args:
        platform: Hosting platform identifier, e.g. ``"github"``.
        owner: Repository owner (organisation or user), e.g. ``"anthropics"``.
        name: Repository name, e.g. ``"anthropic-sdk-python"``.

    Returns:
        str: The first 16 hexadecimal characters of the SHA-256 digest of the
        key string ``"{platform}:{owner}/{name}"``.

    Example:
        >>> get_repo_hash("github", "owner", "repo")
        '...'  # deterministic 16-char hex string
    """
    key = f"{platform}:{owner}/{name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def filter_files(
    root: Path,
    max_file_bytes: int = 1024 * 1024,  # 1MB per file
    ignore_file: Path | None = None,
) -> list[Path]:
    """Return all indexable source files under *root*, sorted by path.

    Walks the directory tree recursively and applies the following filters in
    order:

    1. Skip directories listed in :data:`EXCLUDED_DIRS` (build artefacts,
       caches, VCS metadata, etc.).
    2. Skip files whose extension is not in :data:`SOURCE_EXTENSIONS`.
    3. Skip files larger than *max_file_bytes* (default 1 MB).
    4. If *ignore_file* is provided and is a valid ``.gitignore``-style file,
       skip files whose relative path matches any pattern in that file.
       AutoWiki uses ``{clone_dir}/.autowikiignore`` by convention.

    Args:
        root: Absolute path to the repository root directory to walk.
        max_file_bytes: Maximum file size in bytes; files larger than this are
            excluded.  Defaults to ``1_048_576`` (1 MiB).
        ignore_file: Optional path to a ``.gitignore``-style ignore file
            (e.g. ``root / ".autowikiignore"``).  If ``None`` or the file does
            not exist, no additional patterns are applied.

    Returns:
        list[Path]: Absolute ``Path`` objects for all files that passed every
        filter, sorted lexicographically by their full path.

    Example:
        >>> files = filter_files(Path("/tmp/my-repo"))
        >>> [str(f.relative_to("/tmp/my-repo")) for f in files[:3]]
        ['README.md', 'src/main.py', 'src/utils.py']

    Note:
        To exclude additional paths from indexing, create a
        ``.autowikiignore`` file in the repository root using the same
        ``.gitignore`` pattern syntax.
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
    """Extract README content from the repository root directory.

    Tries several common README filenames in priority order and returns the
    content of the first one found, truncated to *max_chars* characters.  The
    content is used as context in the wiki-plan prompt to give the LLM a
    high-level description of the project.

    Args:
        root: Absolute path to the repository root directory.
        max_chars: Maximum number of characters to return.  Defaults to
            ``3000`` to keep the LLM prompt manageable.

    Returns:
        str | None: The first *max_chars* characters of the README content, or
        ``None`` if no recognised README file exists or all reads fail.

    Example:
        >>> readme = extract_readme(Path("/tmp/my-repo"))
        >>> readme[:50] if readme else "No README found"
        '# My Project\\n\\nA short description...'
    """
    for name in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
        p = root / name
        if p.exists() and p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")[:max_chars]
            except OSError:
                continue
    return None


async def clone_or_fetch(clone_dir: Path, owner: str, name: str) -> str:
    """Clone a GitHub repository, or fetch and reset an existing clone.

    Performs a *shallow* clone (``depth=1``) on first run to minimise disk
    usage and network time.  On subsequent calls it fetches the latest changes
    from ``origin`` and hard-resets ``HEAD`` to ``FETCH_HEAD``.

    Blocking gitpython I/O is offloaded to the default thread-pool executor
    via :func:`asyncio.get_event_loop().run_in_executor` so the ARQ event
    loop is never blocked.

    Args:
        clone_dir: Local directory where the repository should be cloned.
            Created automatically if it does not exist.
        owner: GitHub repository owner (organisation or user).
        name: GitHub repository name.

    Returns:
        str: The 40-character hexadecimal SHA of the HEAD commit after the
        clone or fetch completes.

    Raises:
        git.exc.GitCommandError: If the remote is unreachable, authentication
            fails, or any git operation returns a non-zero exit code.

    Example:
        >>> sha = await clone_or_fetch(Path("/tmp/clones/my-repo"), "owner", "repo")
        >>> len(sha)
        40
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


async def get_changed_files(clone_dir: Path, old_sha: str, new_sha: str) -> list[str]:
    """Return the list of file paths that changed between two git commits.

    Uses ``git diff --name-only`` to determine which files were added,
    modified, or deleted between *old_sha* and *new_sha*.  The diff runs in a
    thread executor to avoid blocking the event loop.

    Args:
        clone_dir: Path to the local git repository (must contain a ``.git``
            directory).
        old_sha: The earlier commit SHA (base of the diff).
        new_sha: The later commit SHA (tip of the diff).

    Returns:
        list[str]: Relative file paths (as reported by git) for every file
        that was added, modified, or deleted between the two commits.  Returns
        an empty list if there are no differences.

    Raises:
        git.exc.GitCommandError: If either SHA is not reachable in the
            repository history — for example because a shallow clone does not
            contain *old_sha*.

    Example:
        >>> changed = await get_changed_files(
        ...     Path("/tmp/clones/my-repo"), "abc123", "def456"
        ... )
        >>> changed
        ['src/main.py', 'tests/test_main.py']
    """
    import asyncio

    import git

    def _do_diff() -> list[str]:
        repo = git.Repo(clone_dir)
        diff_output = repo.git.diff("--name-only", old_sha, new_sha)
        if not diff_output:
            return []
        return [line for line in diff_output.split("\n") if line.strip()]

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _do_diff)


def get_affected_pages(changed_files: list[str], wiki_plan: WikiPlan) -> set[str]:
    """Return titles of wiki pages whose assigned files overlap with changed_files.

    Used during incremental refresh to determine which wiki pages need to be
    regenerated: only pages that reference at least one of the changed files
    are included in the result.

    Args:
        changed_files: List of relative file paths (as returned by
            :func:`get_changed_files`) that have been modified.
        wiki_plan: The :class:`~worker.pipeline.wiki_planner.WikiPlan`
            describing the current page-to-file assignments.

    Returns:
        set[str]: Page titles (strings matching ``WikiPageSpec.title``) for
        every page that references at least one changed file.

    Example:
        >>> from worker.pipeline.wiki_planner import WikiPlan, WikiPageSpec
        >>> plan = WikiPlan(pages=[
        ...     WikiPageSpec(title="API Layer", purpose="...",
        ...                  files=["api/routes.py", "api/models.py"]),
        ...     WikiPageSpec(title="Worker", purpose="...",
        ...                  files=["worker/jobs.py"]),
        ... ])
        >>> get_affected_pages(["api/routes.py"], plan)
        {'API Layer'}
        >>> get_affected_pages(["api/routes.py", "worker/jobs.py"], plan)
        {'API Layer', 'Worker'}
        >>> get_affected_pages(["unrelated/file.py"], plan)
        set()
    """
    changed = set(changed_files)
    affected: set[str] = set()
    for page in wiki_plan.pages:
        if any(f in changed for f in (page.files or [])):
            affected.add(page.title)
    return affected
