"""`.autowiki/wiki.json` loader and user-steering dataclasses.

Invoked during Stage 1 (ingestion) after the repo is cloned. The returned
:class:`UserSteering` (or ``None``) is passed through to the wiki planner
and page generator so user-authored notes and outlines can influence
both structure and content.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("worker.user_steering")


@dataclass
class UserPageSpec:
    title: str
    purpose: str | None = None
    parent: str | None = None
    modules: list[str] = field(default_factory=list)
    page_notes: list[str] = field(default_factory=list)


@dataclass
class UserSteering:
    repo_notes: list[str] = field(default_factory=list)
    pages: list[UserPageSpec] = field(default_factory=list)


def load_user_steering(clone_root: Path) -> UserSteering | None:
    """Load ``{clone_root}/.autowiki/wiki.json``.

    Returns ``None`` when the file is missing or invalid. Warnings are
    logged for invalid files so users can see what went wrong in the job
    log without failing the whole pipeline.
    """
    path = clone_root / ".autowiki" / "wiki.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Invalid .autowiki/wiki.json: %s", e)
        return None
    if not isinstance(data, dict):
        logger.warning("Invalid .autowiki/wiki.json: top-level must be an object")
        return None

    repo_notes = data.get("repo_notes") or []
    if not isinstance(repo_notes, list):
        logger.warning(".autowiki/wiki.json repo_notes must be a list; ignoring")
        repo_notes = []
    norm_notes: list[str] = []
    for n in repo_notes:
        if isinstance(n, str):
            norm_notes.append(n)
        elif isinstance(n, dict) and isinstance(n.get("content"), str):
            norm_notes.append(n["content"])

    raw_pages = data.get("pages") or []
    if not isinstance(raw_pages, list):
        logger.warning(".autowiki/wiki.json pages must be a list; ignoring")
        raw_pages = []

    pages: list[UserPageSpec] = []
    for p in raw_pages:
        if not isinstance(p, dict):
            continue
        title = p.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        modules_raw = p.get("modules") or []
        if not isinstance(modules_raw, list):
            logger.warning(
                ".autowiki/wiki.json page %r modules must be a list; ignoring",
                title,
            )
            modules_raw = []
        modules = [m for m in modules_raw if isinstance(m, str)]

        notes_raw = p.get("page_notes") or []
        if not isinstance(notes_raw, list):
            logger.warning(
                ".autowiki/wiki.json page %r page_notes must be a list; ignoring",
                title,
            )
            notes_raw = []
        page_notes = [
            n if isinstance(n, str) else n.get("content", "")
            for n in notes_raw
            if isinstance(n, str)
            or (isinstance(n, dict) and isinstance(n.get("content"), str))
        ]
        page_notes = [n for n in page_notes if n]
        purpose = p.get("purpose")
        parent = p.get("parent")
        pages.append(
            UserPageSpec(
                title=title.strip(),
                purpose=(purpose if isinstance(purpose, str) else None),
                parent=(parent if isinstance(parent, str) else None),
                modules=modules,
                page_notes=page_notes,
            )
        )

    return UserSteering(repo_notes=norm_notes, pages=pages)


def assign_by_modules(
    pages: list[UserPageSpec], all_files: list[str]
) -> tuple[dict[str, list[str]], list[str]]:
    """Pre-assign files to pages by longest-prefix match on ``modules``.

    Returns ``(assignments, unassigned)`` where ``assignments`` maps each
    page title to a list of matched files and ``unassigned`` lists the
    files that did not match any user module prefix.
    """
    assignments: dict[str, list[str]] = {p.title: [] for p in pages}
    unassigned: list[str] = []
    # Sort prefixes longest-first so nested directories win.
    prefix_owners: list[tuple[str, str]] = sorted(
        ((prefix.rstrip("/"), p.title) for p in pages for prefix in p.modules),
        key=lambda t: len(t[0]),
        reverse=True,
    )
    for file in all_files:
        matched = False
        for prefix, owner in prefix_owners:
            if file == prefix or file.startswith(prefix + "/"):
                assignments[owner].append(file)
                matched = True
                break
        if not matched:
            unassigned.append(file)
    return assignments, unassigned
