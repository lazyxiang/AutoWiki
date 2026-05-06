"""Deprecated: import from ``worker.pipeline.page.generator`` instead."""

from __future__ import annotations

import warnings

from worker.pipeline.page import generator as _new_module
from worker.pipeline.page.generator import *  # noqa: F401, F403

warnings.warn(
    "worker.pipeline.page_generator is deprecated; import from "
    "worker.pipeline.page.generator instead",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export private names too so existing imports of underscored helpers
# (e.g. ``_extract_signature_slices``, ``_balance_chunks``) keep working.
for _name in dir(_new_module):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_new_module, _name)
del _name
