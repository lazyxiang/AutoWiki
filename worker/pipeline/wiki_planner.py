"""Deprecated: import from ``worker.pipeline.planner.wiki_planner`` instead."""

from __future__ import annotations

import warnings

from worker.pipeline.planner import wiki_planner as _new_module
from worker.pipeline.planner.wiki_planner import *  # noqa: F401, F403

warnings.warn(
    "worker.pipeline.wiki_planner is deprecated; import from "
    "worker.pipeline.planner.wiki_planner instead",
    DeprecationWarning,
    stacklevel=2,
)

for _name in dir(_new_module):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_new_module, _name)
del _name
