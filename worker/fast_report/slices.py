"""Deprecated source slice extraction compatibility module."""

from __future__ import annotations

import warnings

from worker.pipeline.retrieval import code_slices as _code_slices
from worker.pipeline.retrieval.code_slices import *  # noqa: F403

__all__ = _code_slices.__all__

warnings.warn(
    "worker.fast_report.slices is deprecated; import "
    "worker.pipeline.retrieval.code_slices instead",
    DeprecationWarning,
    stacklevel=2,
)
