"""Deprecated compatibility shim for repository retrieval indexes."""

from __future__ import annotations

import warnings

from worker.pipeline.retrieval import repo_index as _repo_index

warnings.warn(
    "worker.pipeline.fast_report_index is deprecated; use "
    "worker.pipeline.retrieval.repo_index instead.",
    DeprecationWarning,
    stacklevel=2,
)

REPO_INDEX_VERSION = _repo_index.REPO_INDEX_VERSION
INDEX_VERSION = REPO_INDEX_VERSION
build_repo_index = _repo_index.build_repo_index
build_fast_report_index = build_repo_index

for _name in dir(_repo_index):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_repo_index, _name)

__all__ = [
    "INDEX_VERSION",
    "REPO_INDEX_VERSION",
    "build_fast_report_index",
    "build_repo_index",
]
