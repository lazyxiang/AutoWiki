"""Load and validate persisted repository retrieval indexes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from worker.pipeline.retrieval.repo_index import REPO_INDEX_VERSION

_NEW_NAME = "repo_index.json"
_LEGACY_NAME = "fast_report_index.json"


class RepoIndexMissingError(FileNotFoundError):
    """Raised when no repository index artifact exists."""


class RepoIndexOutdatedError(RuntimeError):
    """Raised when a repository index artifact is too old for retrieval."""


def load_repo_index(repo_data_dir: Path) -> dict[str, Any]:
    """Load ``ast/repo_index.json``, migrating the legacy artifact name if needed."""
    ast_dir = repo_data_dir / "ast"
    new_path = ast_dir / _NEW_NAME
    legacy_path = ast_dir / _LEGACY_NAME

    if not new_path.exists():
        if legacy_path.exists():
            legacy_path.replace(new_path)
        else:
            raise RepoIndexMissingError(
                f"Repository index not found at {new_path} or {legacy_path}"
            )

    loaded = json.loads(new_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise RepoIndexOutdatedError(
            "fast_report_index_outdated: Repository index payload must be an object. "
            "Run `autowiki index <repo>` to upgrade."
        )
    validate_repo_index_version(loaded)
    return loaded


def validate_repo_index_version(data: dict) -> None:
    """Validate that a loaded repository index matches the current schema."""
    version = data.get("index_version", 0)
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < REPO_INDEX_VERSION
    ):
        raise RepoIndexOutdatedError(
            "fast_report_index_outdated: Repository index is outdated for fast "
            f"reports (found={version!r}, expected={REPO_INDEX_VERSION}). "
            "Run `autowiki index <repo>` to upgrade."
        )
