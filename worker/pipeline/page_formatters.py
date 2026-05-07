"""Deprecated: import from ``worker.pipeline.page.formatters`` instead."""

from __future__ import annotations

import warnings

from worker.pipeline.page import formatters as _new_module
from worker.pipeline.page.formatters import *  # noqa: F401, F403

warnings.warn(
    "worker.pipeline.page_formatters is deprecated; import from "
    "worker.pipeline.page.formatters instead",
    DeprecationWarning,
    stacklevel=2,
)

for _name in dir(_new_module):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_new_module, _name)
del _name
