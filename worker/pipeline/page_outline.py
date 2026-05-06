"""Deprecated: import from ``worker.pipeline.page.outline`` instead."""

from __future__ import annotations

import warnings

from worker.pipeline.page import outline as _new_module
from worker.pipeline.page.outline import *  # noqa: F401, F403

warnings.warn(
    "worker.pipeline.page_outline is deprecated; import from "
    "worker.pipeline.page.outline instead",
    DeprecationWarning,
    stacklevel=2,
)

for _name in dir(_new_module):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_new_module, _name)
del _name
