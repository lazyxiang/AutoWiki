"""Compatibility exports for index job entry points.

The full-index and refresh orchestrators are implemented in dedicated modules
so their long stage workflows can evolve independently while preserving the
historical ``worker.index.jobs`` import path.
"""

from __future__ import annotations

from worker.index.backup import (
    _cleanup_first_time_index_failure,
    _discard_full_index_backup,
    _repo_has_previous_success,
    _restore_full_index_state,
    _snapshot_full_index_state,
)
from worker.index.full import _repo_metadata_updates, run_full_index
from worker.index.refresh import run_refresh_index

__all__ = [
    "_cleanup_first_time_index_failure",
    "_discard_full_index_backup",
    "_repo_has_previous_success",
    "_repo_metadata_updates",
    "_restore_full_index_state",
    "_snapshot_full_index_state",
    "run_full_index",
    "run_refresh_index",
]
