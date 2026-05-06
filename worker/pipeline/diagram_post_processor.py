"""Deprecated: import from ``worker.pipeline.page.diagram_post_processor`` instead."""

from __future__ import annotations

import warnings

from worker.pipeline.page import diagram_post_processor as _new_module
from worker.pipeline.page.diagram_post_processor import *  # noqa: F401, F403

warnings.warn(
    "worker.pipeline.diagram_post_processor is deprecated; import from "
    "worker.pipeline.page.diagram_post_processor instead",
    DeprecationWarning,
    stacklevel=2,
)

for _name in dir(_new_module):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_new_module, _name)
del _name
