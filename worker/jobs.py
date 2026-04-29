"""Compatibility facade for ARQ job entrypoints.

The concrete job implementations live in focused modules:

- ``worker.index.full`` for full-index orchestration.
- ``worker.index.refresh`` for refresh orchestration.
- ``worker.fast_report.jobs`` for Fast Report generation.
- ``worker.research.jobs`` for Deep Research.

This module keeps existing queued ARQ function names and legacy imports stable.
New code should import from the focused modules directly.
"""

from __future__ import annotations

from worker.fast_report.jobs import (
    FastReportIndexOutdated as FastReportIndexOutdated,
)
from worker.fast_report.jobs import (
    _build_default_fast_report_retrievers as _build_default_fast_report_retrievers,
)
from worker.fast_report.jobs import _load_fast_report_index as _load_fast_report_index
from worker.fast_report.jobs import (
    _load_fast_report_wiki_pages as _load_fast_report_wiki_pages,
)
from worker.fast_report.jobs import (
    _missing_fast_report_retriever as _missing_fast_report_retriever,
)
from worker.fast_report.jobs import _readme_first_paragraph as _readme_first_paragraph
from worker.fast_report.jobs import (
    _validate_fast_report_index_version as _validate_fast_report_index_version,
)
from worker.fast_report.jobs import run_fast_report as run_fast_report
from worker.index.jobs import _repo_metadata_updates as _repo_metadata_updates
from worker.index.jobs import run_full_index as run_full_index
from worker.index.jobs import run_refresh_index as run_refresh_index
from worker.research.jobs import _load_faiss_for_research as _load_faiss_for_research
from worker.research.jobs import run_deep_research as run_deep_research

__all__ = [
    "FastReportIndexOutdated",
    "_build_default_fast_report_retrievers",
    "_load_faiss_for_research",
    "_load_fast_report_index",
    "_load_fast_report_wiki_pages",
    "_missing_fast_report_retriever",
    "_readme_first_paragraph",
    "_repo_metadata_updates",
    "_validate_fast_report_index_version",
    "run_deep_research",
    "run_fast_report",
    "run_full_index",
    "run_refresh_index",
]
