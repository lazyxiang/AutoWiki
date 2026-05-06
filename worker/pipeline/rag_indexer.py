"""Deprecated: import from ``worker.pipeline.retrieval.rag_indexer`` instead."""

from __future__ import annotations

import warnings

from worker.pipeline.retrieval import rag_indexer as _new_module
from worker.pipeline.retrieval.rag_indexer import *  # noqa: F401, F403

warnings.warn(
    "worker.pipeline.rag_indexer is deprecated; import from "
    "worker.pipeline.retrieval.rag_indexer instead",
    DeprecationWarning,
    stacklevel=2,
)

for _name in dir(_new_module):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_new_module, _name)
del _name
