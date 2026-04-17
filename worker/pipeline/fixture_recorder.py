"""Record planner intermediate artefacts for offline replay.

Gated on the ``AUTOWIKI_RECORD_PLANNER_FIXTURES`` env var so production
runs do not pay the I/O cost.  Recorded fixtures are consumed by
``autowiki validate-plan`` to produce a diagnostic report without
spending live LLM budget.

Fixture layout (relative to the per-repo data dir)::

    fixtures/
      outline.json          # Phase-1 outline (list of page dicts)
      assignments.json      # {"primary": {...}, "secondary": {...}}
      wiki_plan.json        # final validated plan (same shape as
                            # ast/wiki_plan.json; duplicated so the
                            # fixtures dir is self-contained)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("worker.planner.recorder")


def is_recording_enabled() -> bool:
    """Return True when ``AUTOWIKI_RECORD_PLANNER_FIXTURES=1`` in the env."""
    return os.environ.get("AUTOWIKI_RECORD_PLANNER_FIXTURES", "0") == "1"


class FixtureRecorder:
    """Writes JSON fixtures under ``root``.  No-op when ``root is None``."""

    def __init__(self, root: Path | None) -> None:
        self.root = root

    async def _ensure_root(self) -> None:
        if self.root is not None:
            import asyncio

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: self.root.mkdir(parents=True, exist_ok=True)
            )

    async def _write(self, filename: str, payload: Any) -> None:
        if self.root is None:
            return
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            # Serialise on the event loop, write in the executor
            text = json.dumps(payload, indent=2, default=_default_encoder)
            await loop.run_in_executor(
                None, lambda: (self.root / filename).write_text(text)
            )
        except Exception as exc:
            logger.error("Failed to record fixture %s: %s", filename, exc)

    async def record_outline(self, outline: list[dict]) -> None:
        await self._ensure_root()
        await self._write("outline.json", outline)

    async def record_assignments(
        self,
        primary: dict[str, list[str]],
        secondary: dict[str, list[str]],
    ) -> None:
        await self._ensure_root()
        await self._write(
            "assignments.json", {"primary": primary, "secondary": secondary}
        )

    async def record_wiki_plan(self, plan: dict) -> None:
        await self._ensure_root()
        await self._write("wiki_plan.json", plan)


def _default_encoder(obj: Any) -> Any:
    # Support dataclasses / sets so callers do not need to flatten manually.
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj)!r} is not JSON serializable")
