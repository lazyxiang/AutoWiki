"""Stage 5 of the generation pipeline — LLM-generated logical wiki page plan.

Given a :class:`~worker.pipeline.ast_analysis.FileAnalysis` and an optional
:class:`~worker.pipeline.dependency_graph.DependencyGraph`, this module asks
the configured LLM to produce a hierarchical wiki plan: a JSON structure that
maps *every* source file in the repository to exactly one page.

The main entry point is :func:`generate_wiki_plan`, which:

1. Builds a text prompt from the file summary, README, and dependency info.
2. Calls the LLM with a structured JSON schema via ``async_retry``.
3. Validates and normalises the response with :func:`validate_wiki_plan`.
4. Retries up to *max_retries* times if validation fails, appending the error
   to the prompt to provide feedback.
5. If Phase 1 (Outline) fails after all retries, the task is terminated with
   a :class:`WikiPlannerError`. If Phase 2 (Assignment) fails, the planner
   recovers using a locality-preserving heuristic while keeping the outline.

The plan is represented as a :class:`WikiPlan` (a list of
:class:`WikiPageSpec` objects) and can be serialised to three different JSON
shapes via :meth:`WikiPlan.to_wiki_json`, :meth:`WikiPlan.to_internal_json`,
and :meth:`WikiPlan.to_api_structure`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from worker.llm.base import LLMProvider
from worker.llm.prompt_segment import PromptSegment
from worker.pipeline.language import get_planner_language_instruction
from worker.pipeline.pipeline_logging import log_final_failure, log_validation_retry
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

if TYPE_CHECKING:
    from worker.pipeline.ast_analysis import FileAnalysis
    from worker.pipeline.dependency_graph import DependencyGraph
    from worker.pipeline.user_steering import UserSteering

logger = logging.getLogger("worker.planner")

#: Hard maximum and soft minimum for representative files per wiki page.
MAX_FILES_PER_PAGE = 10
MIN_FILES_PER_PAGE = 3

_CODE_EXTS: frozenset[str] = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".cpp",
        ".c",
        ".cs",
        ".rb",
        ".swift",
        ".kt",
        ".scala",
    }
)
_DOC_EXTS: frozenset[str] = frozenset({".md", ".rst", ".txt", ".adoc"})
_CONFIG_EXTS: frozenset[str] = frozenset(
    {".json", ".yaml", ".yml", ".toml", ".ini", ".env", ".cfg"}
)


class WikiPlannerError(Exception):
    """Critical error in wiki planner that should stop the generation task.

    This error indicates that Phase 1 (Outline generation) failed completely
    after all retries, or that a user-steering input is unrecoverably invalid.
    Surfacing this error allows the worker to stop the job and report the
    failure to the frontend.
    """


def _slugify_title(text: str) -> str:
    """Create a URL-safe slug from a title, supporting Unicode characters.

    Uses Unicode-aware word characters and falls back to a hash if the
    result would be empty (e.g. for titles with only symbols).
    """
    slug = re.sub(r"[^\w-]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    if not slug:
        return "page-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return slug


def _suggest_page_range(file_count: int, entity_count: int) -> tuple[int, int]:
    """Suggest min/max page count based on repo complexity."""
    if file_count < 10:
        return (3, 6)
    if file_count <= 30:
        return (8, 15) if entity_count >= 50 else (5, 12)
    if file_count <= 100:
        return (15, 35) if entity_count >= 150 else (10, 25)
    if file_count <= 300:
        return (20, 50)
    return (30, 70)


@dataclass
class WikiPageSpec:
    """Specification for a single wiki page within the plan.

    Each :class:`WikiPageSpec` captures everything the page-generator stage
    needs to produce one Markdown page:

    * **title** — Human-readable concept name (e.g. ``"API Gateway"``).
    * **purpose** — One or two sentences explaining what the page covers and
      why a developer would read it.
    * **parent** — Title of the parent page, or ``None`` for top-level pages.
      Stored as the parent's *title string*, not its slug, so that the
      hierarchy survives slug-derivation changes.
    * **page_notes** — Freeform list of note dicts (default one empty note).
      Reserved for future Phase-4 user-steering support.
    * **files** — List of repository-relative file paths assigned to this page
      by the LLM.  Used for RAG retrieval and incremental refresh.

    Note:
        ``slug`` and ``parent_slug`` are *derived* properties computed from
        ``title`` and ``parent`` respectively; they are never stored in the
        dataclass fields to avoid redundancy.
    """

    title: str
    purpose: str  # replaces "description"
    parent: str | None = None  # parent page TITLE string (not slug)
    page_notes: list[dict] = field(default_factory=lambda: [{"content": ""}])
    files: list[str] = field(default_factory=list)  # representative source files

    @property
    def slug(self) -> str:
        """URL-safe slug derived from the page title.

        Converts the title to lowercase, replaces any run of non-alphanumeric
        characters with a hyphen, and strips leading/trailing hyphens.
        Supports Unicode characters (e.g. Chinese titles).

        Returns:
            str: A URL-safe slug suitable for use as a filesystem name and
            URL path segment.

        Example:
            >>> WikiPageSpec(title="API Gateway", purpose="...").slug
            'api-gateway'
            >>> WikiPageSpec(title="中文文档", purpose="...").slug
            '中文文档'
        """
        return _slugify_title(self.title)

    @property
    def parent_slug(self) -> str | None:
        """URL-safe slug derived from the parent page title.

        Applies the same slug-derivation logic as :attr:`slug` to
        :attr:`parent`.

        Returns:
            str | None: The parent page's slug, or ``None`` if this page has
            no parent (i.e. it is a top-level page).

        Example:
            >>> spec = WikiPageSpec(title="Routes", purpose="...",
            ...                     parent="API Gateway")
            >>> spec.parent_slug
            'api-gateway'
        """
        if self.parent is None:
            return None
        return _slugify_title(self.parent)


@dataclass
class WikiPlan:
    """Container for the full set of wiki pages produced by the planner.

    Holds optional repository-level notes (``repo_notes``) and the ordered
    list of :class:`WikiPageSpec` objects that make up the planned wiki.

    The three serialisation methods produce different JSON shapes for
    different consumers:

    * :meth:`to_wiki_json` — user-facing ``wiki.json`` (no slugs, no files).
    * :meth:`to_internal_json` — pipeline-internal ``ast/wiki_plan.json``
      (includes ``files`` for incremental refresh).
    * :meth:`to_api_structure` — API response shape (includes derived
      ``slug``/``parent_slug`` for the frontend).
    """

    repo_notes: list[dict] = field(default_factory=lambda: [{"content": ""}])
    pages: list[WikiPageSpec] = field(default_factory=list)
    all_repo_files: list[str] = field(default_factory=list)

    def to_wiki_json(self) -> dict:
        """Serialise to the user-facing ``wiki.json`` format.

        Omits ``slug``, ``parent_slug``, and ``files`` fields so the file
        remains human-editable for future Phase-4 user-steering.

        Returns:
            dict: A dictionary with keys:

            * ``"repo_notes"`` (list[dict]): Repository-level notes.
            * ``"pages"`` (list[dict]): Each page dict has ``"title"``,
              ``"purpose"``, ``"page_notes"``, and optionally ``"parent"``.

        Example:
            >>> plan = WikiPlan(pages=[WikiPageSpec(
            ...     title="Overview", purpose="Project overview.")])
            >>> plan.to_wiki_json()
            {'repo_notes': [{'content': ''}],
             'pages': [{'title': 'Overview', 'purpose': 'Project overview.',
                        'page_notes': [{'content': ''}]}]}
        """
        return {
            "repo_notes": self.repo_notes,
            "pages": [
                {
                    "title": p.title,
                    "purpose": p.purpose,
                    "page_notes": p.page_notes,
                    **({"parent": p.parent} if p.parent is not None else {}),
                }
                for p in self.pages
            ],
        }

    def to_internal_json(self) -> dict:
        """Serialise to the pipeline-internal ``ast/wiki_plan.json`` format.

        Includes the ``files`` field for each page so that the incremental
        refresh logic can determine which pages are affected by a given set
        of changed files.

        Returns:
            dict: A dictionary with keys:

            * ``"repo_notes"`` (list[dict]): Repository-level notes.
            * ``"pages"`` (list[dict]): Each page dict has ``"title"``,
              ``"purpose"``, ``"files"``, and optionally ``"parent"``.

        Example:
            >>> plan.to_internal_json()["pages"][0]["files"]
            ['api/routes.py', 'api/models.py']
        """
        return {
            "repo_notes": self.repo_notes,
            "all_repo_files": self.all_repo_files,
            "pages": [
                {
                    "title": p.title,
                    "purpose": p.purpose,
                    "files": p.files,
                    **({"parent": p.parent} if p.parent is not None else {}),
                }
                for p in self.pages
            ],
        }

    def to_api_structure(self) -> dict:
        """Serialise to the API response format consumed by the frontend.

        Derives ``slug`` and ``parent_slug`` from titles and renames
        ``purpose`` to ``description`` to match the existing REST contract.
        Also includes ``has_user_notes`` which is ``True`` when any
        ``page_notes`` entry has a non-empty ``content`` value (indicating
        the page was steered via ``.autowiki/wiki.json``).

        Returns:
            dict: A dictionary with key ``"pages"``, a list of dicts each
            containing ``"title"``, ``"slug"``, ``"parent_slug"``,
            ``"description"``, and ``"has_user_notes"``.

        Example:
            >>> plan.to_api_structure()
            {'pages': [{'title': 'Overview', 'slug': 'overview',
                        'parent_slug': None, 'description': 'Project overview.',
                        'has_user_notes': False}]}
        """

        def _has_user_notes(page: WikiPageSpec) -> bool:
            return any(n.get("content") for n in page.page_notes)

        return {
            "pages": [
                {
                    "title": p.title,
                    "slug": p.slug,
                    "parent_slug": p.parent_slug,
                    "description": p.purpose,
                    "has_user_notes": _has_user_notes(p),
                }
                for p in self.pages
            ]
        }


_OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "purpose": {"type": "string"},
                    "parent": {"type": ["string", "null"]},
                },
                "required": ["title", "purpose"],
            },
        }
    },
    "required": ["pages"],
}

_SELECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "selections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_title": {"type": "string"},
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["page_title", "files"],
            },
        }
    },
    "required": ["selections"],
}

_SYSTEM = (
    "You are a senior technical documentation architect "
    "creating a comprehensive wiki structure for a software "
    "repository. You analyze codebases deeply — examining "
    "file contents, dependency relationships, and code "
    "structure — to produce a well-organized hierarchical "
    "wiki plan that helps developers understand the project "
    "quickly.\n\n"
    "Think step-by-step:\n"
    "1. Read the README to understand the project's purpose "
    "and architecture\n"
    "2. Examine the file-level summaries to identify major "
    "components and patterns\n"
    "3. Use the dependency graph to understand how files "
    "relate to each other\n"
    "4. Group tightly-coupled files into coherent pages based "
    "on semantic purpose. Directories are a strong default "
    "signal — only override directory structure when files in "
    "different directories form a tight semantic unit (e.g. a "
    "frontend component and its backend route).\n"
    "5. Create exactly 2 levels of hierarchy: top-level pages "
    "for major subsystems (e.g. 'Worker Pipeline', 'API Layer', "
    "'Frontend'), and child pages for specific topics within "
    "each subsystem. Do NOT create a single root page that is "
    "the parent of all other pages — every top-level page must "
    "represent a coherent subsystem, not a catch-all container.\n\n"
    "Each page should have a clear PURPOSE — it should "
    "explain a concept, component, or workflow. Pages are "
    "represented by 5\u20138 of their most representative source "
    "code files \u2014 full coverage of every file is not required.\n\n"
    "Output ONLY valid JSON."
)


def _build_outline_prompt(
    file_summary: str,
    repo_name: str,
    readme: str | None = None,
    dep_info: str | None = None,
    clusters: list[list[str]] | None = None,
    page_range: tuple[int, int] = (5, 20),
    anchors_block: str | None = None,
) -> str:
    """Build the Phase 1 prompt: generate page tree without file assignments."""
    sections = [f"Repository: {repo_name}"]

    if readme:
        sections.append(f"README:\n{readme}")

    if anchors_block:
        sections.append(
            "Architectural anchors:\n"
            + anchors_block
            + "\n\nThe largest internal packages shown above are usually "
            "meaningful architectural units. Use them as hierarchy hints "
            "unless dependency analysis says otherwise."
        )

    sections.append(f"File summaries:\n{file_summary}")

    if dep_info:
        sections.append(f"Dependency relationships:\n{dep_info}")

    if clusters:
        # Show small clusters (≤20 files) as explicit file lists — they carry
        # actionable grouping signal.  Large clusters span most of the repo
        # through shared utilities; listing all their files is noise since the
        # dependency graph already captures those relationships.
        _CLUSTER_DETAIL_LIMIT = 20
        cluster_strs: list[str] = []
        shown = 0
        for c in clusters:
            if shown >= 30:
                break
            if len(c) <= _CLUSTER_DETAIL_LIMIT:
                cluster_strs.append(f"  Cluster ({len(c)} files): {', '.join(c)}")
            else:
                # Derive the dominant package prefix from the cluster files
                # (e.g. "worker/pipeline" for a cluster of worker/pipeline/*.py)
                prefixes: dict[str, int] = {}
                for f in c:
                    parts = f.split("/")
                    pkg = "/".join(parts[:-1]) if len(parts) > 1 else "(root)"
                    prefixes[pkg] = prefixes.get(pkg, 0) + 1
                top_pkg = max(prefixes, key=lambda k: prefixes[k])
                cluster_strs.append(
                    f"  Large cluster: {top_pkg}/* ({len(c)} files) — "
                    "likely a single subsystem; consider one parent page "
                    "with sub-pages per file role"
                )
            shown += 1
        remaining = len(clusters) - shown
        if remaining > 0:
            cluster_strs.append(f"  ... and {remaining} more clusters")
        sections.append(
            "File clusters (files that import each other):\n" + "\n".join(cluster_strs)
        )

    schema_json = json.dumps(_OUTLINE_SCHEMA, indent=2)
    min_pages, max_pages = page_range
    sections.append(
        "Create a hierarchical wiki plan. Guidelines:\n"
        f"- Create between {min_pages} and {max_pages} pages. Prefer more granular "
        "pages over broad ones \u2014 a focused page covering 5\u20138 representative "
        "code files is better than a sprawling page covering 20+. Each page should "
        "have a clear, single responsibility.\n"
        "- Each page MUST have: title (descriptive, concept-oriented) and "
        "purpose (1-2 sentences explaining WHAT the page covers and WHY a "
        "developer would read it)\n"
        "- Optionally set parent (title of parent page) for hierarchy\n"
        "- Use EXACTLY 2 levels of hierarchy: top-level pages represent major "
        "subsystems (e.g. 'Worker Pipeline', 'API Layer', 'Frontend UI'), child "
        "pages cover specific topics within that subsystem. Pages may have at most "
        "one level of children — grandchild pages are not allowed.\n"
        "- Do NOT create a single omnibus root page (e.g. 'Project Overview' or "
        "'Documentation Home') as the parent of all other top-level pages. "
        "Every top-level page should stand on its own as a coherent subsystem.\n"
        "- Group by semantic purpose. Directories are a strong default "
        "signal — only override directory structure when files in different "
        "directories form a tight semantic unit (e.g. a frontend component "
        "and its backend route).\n"
        "- Page titles should describe concepts/components, not directory names\n"
        "- Do NOT assign files to pages — just define the page structure\n\n"
        "Output JSON matching this schema:\n"
        f"{schema_json}"
    )

    return "\n\n".join(sections)


def _build_selection_system(
    file_summary: str,
    dep_info: str | None,
) -> list[PromptSegment]:
    parts: list[str] = [
        "You are selecting representative source files for wiki pages.",
        "",
        "## Repository file summaries:",
        file_summary,
    ]
    if dep_info:
        parts += ["", "## Dependency relationships:", dep_info]
    return [PromptSegment(text="\n".join(parts), cacheable=True)]


def _build_selection_user(
    pages_with_candidates: list[tuple[str, str, list[str]]],
    last_error: str | None = None,
) -> PromptSegment:
    schema_json = json.dumps(_SELECTION_SCHEMA, indent=2)
    pages_str = "\n\n".join(
        f'Page: "{title}"\nPurpose: {purpose}\nCandidates:\n'
        + "\n".join(f"  - {f}" for f in candidates)
        for title, purpose, candidates in pages_with_candidates
    )
    text = (
        f"For each wiki page below, select the "
        f"{MIN_FILES_PER_PAGE}–{MAX_FILES_PER_PAGE} "
        "source code files from its candidate list that best represent "
        "the page's content.\n\n"
        "Rules:\n"
        "- Strongly prefer code files (.py, .ts, .go, .rs, .java, etc.) over "
        ".md / .yaml / .json files\n"
        "- Include only files that contain substantial relevant code "
        "(functions, classes, core logic)\n"
        "- Configuration files only when central to understanding "
        "this page's architecture\n"
        "- README.md only on a top-level Overview page\n"
        "- Target 5–8 files per page; fewer is fine when fewer candidates "
        "are relevant\n"
        "- You may select fewer than 3 only when genuinely fewer relevant "
        "files exist\n\n"
        f"Pages:\n{pages_str}\n\n"
    )
    if last_error:
        text += f"CRITICAL: Previous attempt failed with error: {last_error}\n\n"
    text += f"Output JSON matching this schema:\n{schema_json}"
    return PromptSegment(text=text, cacheable=False)


def _validate_outline_structure(
    pages: list[dict],
    page_range: tuple[int, int],
    total_file_count: int,
) -> None:
    """Validate structural properties of a page outline (no file assignments needed).

    Called inside :func:`_generate_outline` after each LLM response so that
    outline-level errors are caught and retried in Phase 1, before Phase 2
    (file assignment) is even attempted.

    Checks:
    - Duplicate slugs (two titles that normalise to the same URL slug).
    - Parent cycles (circular parent references).
    - Hierarchy not exactly 2 levels deep.
    - Flat plan (all pages top-level) when there are multiple pages.
    - Single omnibus root with all others as children.
    - Page count below the suggested minimum.

    Raises:
        ValueError: Describing the first constraint that is violated.
    """
    slug_counts: dict[str, int] = {}
    for p in pages:
        slug = _slugify_title(p["title"])
        slug_counts[slug] = slug_counts.get(slug, 0) + 1
    dupes = [s for s, cnt in slug_counts.items() if cnt > 1]
    if dupes:
        raise ValueError(f"Duplicate page slugs detected: {', '.join(dupes)}")

    title_to_parent: dict[str, str | None] = {
        p["title"]: p.get("parent") for p in pages
    }
    known = set(title_to_parent)

    def _depth(title: str) -> int:
        d, current, seen = 1, title, {title}
        while (par := title_to_parent.get(current)) is not None:
            if par not in known:
                break  # dangling reference — treated as top-level
            if par in seen:
                raise ValueError(
                    f"Wiki hierarchy contains a parent cycle involving '{par}'"
                )
            seen.add(par)
            current = par
            d += 1
        return d

    max_depth = max((_depth(p["title"]) for p in pages), default=1)
    if max_depth > 2:
        raise ValueError(
            f"Wiki hierarchy is {max_depth} levels deep — use exactly 2 levels: "
            "top-level pages for major subsystems, child pages for specific topics"
        )
    if max_depth == 1 and len(pages) > 1:
        raise ValueError(
            "All pages are top-level — use exactly 2 levels: top-level subsystem "
            "pages with child topic pages"
        )
    top_level = [p for p in pages if not p.get("parent")]
    if len(top_level) == 1 and len(pages) > 2:
        raise ValueError(
            "Single-root wiki plans are not allowed — top-level pages must be "
            "coherent subsystems, not a catch-all container"
        )
    if len(pages) < page_range[0]:
        raise ValueError(
            f"Plan has {len(pages)} pages but minimum is {page_range[0]} — "
            "create more granular pages"
        )


def _validate_selections(
    result: dict[str, list[str]],
    outline: list[dict],
) -> None:
    """Validate per-page file selection counts.

    Called inside :func:`_select_files` after each LLM response so that
    selection-level errors are caught and retried in Phase 2.

    Checks:
    - No page has more than MAX_FILES_PER_PAGE files.
    - No non-overview, non-parent leaf page has zero files.

    Raises:
        ValueError: Describing the first constraint violated.
    """
    all_parents = {p.get("parent") for p in outline if p.get("parent")}
    for page in outline:
        title = page["title"]
        files = result.get(title, [])
        if len(files) > MAX_FILES_PER_PAGE:
            raise ValueError(
                f"VALIDATION_FAILURE: Page '{title}' has {len(files)} files "
                f"(max {MAX_FILES_PER_PAGE}). Reduce to the most representative files."
            )
        is_overview = "overview" in title.lower()
        is_parent = title in all_parents
        if not (is_overview or is_parent or files):
            raise ValueError(
                f"VALIDATION_FAILURE: Page '{title}' has no files selected. "
                "Select at least one representative source file or remove this page."
            )


async def _generate_outline(
    file_summary: str,
    repo_name: str,
    llm: LLMProvider,
    readme: str | None,
    dep_info: str | None,
    clusters: list[list[str]] | None,
    page_range: tuple[int, int],
    system: str,
    on_retry: OnRetryCallback | None,
    max_retries: int = 3,
    total_file_count: int = 0,
    _extra_context: str | None = None,
    anchors_block: str | None = None,
) -> list[dict]:
    """Phase 1: Generate page tree and validate outline structure.

    Combines LLM generation with immediate structural validation so that
    outline-level problems (duplicate slugs, cycles, wrong depth, flat plan,
    too few pages) are caught and retried within this phase rather than being
    deferred to a post-assignment validation step.
    """
    if max_retries < 1:
        raise WikiPlannerError("max_retries must be >= 1")

    prompt = _build_outline_prompt(
        file_summary=file_summary,
        repo_name=repo_name,
        readme=readme,
        dep_info=dep_info,
        clusters=clusters,
        page_range=page_range,
        anchors_block=anchors_block,
    )
    if _extra_context:
        prompt += f"\n\n{_extra_context}"

    for attempt in range(max_retries):
        try:
            raw = await async_retry(
                llm.generate_structured,
                prompt,
                schema=_OUTLINE_SCHEMA,
                system=system,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
            pages = raw.get("pages", [])
            if not pages:
                raise ValueError("Outline has no pages")
            for p in pages:
                if "title" not in p or "purpose" not in p:
                    raise ValueError(f"Page missing title or purpose: {p}")
            _validate_outline_structure(pages, page_range, total_file_count)
            return pages
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                log_validation_retry(
                    logger,
                    stage="wiki_planner.outline",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    exc=e,
                    context={
                        "total_files": total_file_count,
                        "page_range": f"{page_range[0]}-{page_range[1]}",
                    },
                )
                prompt += (
                    f"\n\nPrevious attempt failed with error: {e}. "
                    "Please fix and retry."
                )
            else:
                log_final_failure(
                    logger,
                    stage="wiki_planner.outline",
                    exc=e,
                    context={
                        "total_files": total_file_count,
                        "max_retries": max_retries,
                    },
                )
                raise WikiPlannerError(
                    f"Failed to generate a valid wiki outline: {e}"
                ) from e


_ROOT_ENTRY_POINTS = frozenset(
    {
        "main.py",
        "app.py",
        "cli.py",
        "server.py",
        "manage.py",
        "wsgi.py",
        "asgi.py",
        "index.js",
        "index.ts",
        "index.mjs",
        "main.go",
        "main.rs",
        "lib.rs",
        "main.java",
    }
)

_LOW_PRIORITY_SEGMENTS = frozenset(
    {
        "test",
        "tests",
        "spec",
        "specs",
        "docs",
        "doc",
        "examples",
        "example",
        "bench",
        "benchmarks",
        "mocks",
        "mock",
        "fixtures",
        "testdata",
        "vendor",
        "third_party",
    }
)


def _is_high_priority_file(path: str) -> bool:
    """Determine if a file is a 'core' source file that MUST be assigned to a page.

    To maintain a high-quality, focused wiki, we only enforce assignment for
    files that contain project-specific business or architectural logic.
    Low-priority files (tests, specs, documentation, examples, and root-level
    configs) are allowed to be omitted from the wiki plan.

    Args:
        path: Repository-relative path to the file.

    Returns:
        True if the file is 'core' logic, False if it is low-priority residue.
    """
    lower = path.lower()
    # Ignore dotfiles
    if lower.startswith("."):
        return False
    parts = lower.split("/")
    filename = parts[-1]
    # Check each directory segment against known low-priority names
    dir_segments = parts[:-1]
    if any(seg in _LOW_PRIORITY_SEGMENTS for seg in dir_segments):
        return False
    # Check filename prefix/suffix patterns for test and spec files
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    if (
        stem.startswith("test_")
        or stem.endswith("_test")
        or stem.startswith("spec_")
        or stem.endswith("_spec")
        or stem.endswith(".test")
        or stem.endswith(".spec")
    ):
        return False
    # Root-level entry points are high-priority despite having no directory
    if "/" not in path:
        return filename in _ROOT_ENTRY_POINTS
    return True


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens with length ≥ 3.  Used for page↔directory matching."""
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3}


def _directory_key(rel_path: str) -> str:
    """Return the first directory segment of ``rel_path``.

    Files at the repo root return ``""``.
    """
    parts = rel_path.split("/", 1)
    return parts[0] if len(parts) > 1 else ""


def _best_matching_page(
    dir_key: str,
    page_tokens: dict[str, set[str]],
    sample_files: list[str],
) -> str | None:
    """Return the title of the page whose tokens best match *dir_key*.

    Scoring:
    * +3 per overlapping token between dir_key and page tokens.
    * +1 per overlapping token between any word in sample file basenames
      and the page tokens.
    * Ties broken by page order (first listed wins).
    """
    candidate_tokens: set[str] = _tokenize(dir_key)
    for f in sample_files[:5]:
        candidate_tokens |= _tokenize(f.replace("/", " "))

    best_title: str | None = None
    best_score = 0
    for title, tokens in page_tokens.items():
        overlap = candidate_tokens & tokens
        if not overlap:
            continue
        dir_overlap = _tokenize(dir_key) & tokens
        score = len(dir_overlap) * 3 + (len(overlap) - len(dir_overlap))
        if score > best_score:
            best_score = score
            best_title = title
    return best_title


def _score_file_for_page(
    path: str,
    page: dict,
    file_infos: dict[str, Any],
    dep_graph: DependencyGraph | None,
) -> float:
    score = 0.0
    lower = path.lower()
    ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""

    if ext in _CODE_EXTS:
        score += 3.0
    elif ext in _DOC_EXTS:
        basename = lower.rsplit("/", 1)[-1] if "/" in lower else lower
        if "/" not in path and basename.startswith("readme"):
            score += 2.0 if "overview" in page["title"].lower() else -2.0
        else:
            score -= 2.0
    elif ext in _CONFIG_EXTS:
        score -= 1.5

    info = file_infos.get(path)
    if info is not None:
        score += min(len(info.entities) * 0.4, 3.0)

    if dep_graph is not None:
        in_degree = sum(1 for deps in dep_graph.edges.values() if path in deps)
        score += min(in_degree * 0.3, 2.0)

    page_tokens = _tokenize(page["title"] + " " + page.get("purpose", ""))
    file_tokens = _tokenize(path.replace("/", " ").replace("_", " ").replace("-", " "))
    score += len(page_tokens & file_tokens) * 0.5

    return score


def _prefilter_candidates(
    page: dict,
    all_files: list[str],
    file_infos: dict[str, Any],
    dep_graph: DependencyGraph | None,
    max_candidates: int = 25,
) -> list[str]:
    scored = [
        (f, _score_file_for_page(f, page, file_infos, dep_graph)) for f in all_files
    ]
    scored = [(f, s) for f, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [f for f, _ in scored[:max_candidates]]


def _directory_cluster_assign(
    outline: list[dict],
    all_files: list[str],
) -> dict[str, list[str]]:
    """Locality-preserving file assignment fallback.

    Groups files by their top-level directory, then assigns each directory
    group to the page whose title + purpose tokens best match the directory
    name and sample file basenames.  Unmatched files go to the "Overview"
    page if one exists, else to the first outline page.

    When a single page would receive more than :const:`MAX_FILES_PER_PAGE`
    files, the group is split across all pages whose tokens matched the
    directory at all using remaining-capacity filling.

    Args:
        outline: The wiki outline (list of page dicts).
        all_files: List of all files to assign.

    Returns:
        Mapping from page title to list of assigned files.
    """
    page_titles = [p["title"] for p in outline]
    page_tokens: dict[str, set[str]] = {
        p["title"]: _tokenize(p["title"] + " " + p.get("purpose", "")) for p in outline
    }

    # Bucket files by first directory segment
    buckets: dict[str, list[str]] = {}
    for f in all_files:
        buckets.setdefault(_directory_key(f), []).append(f)

    result: dict[str, list[str]] = {t: [] for t in page_titles}

    # Overview fallback target
    overview_title = next(
        (t for t in page_titles if "overview" in t.lower()),
        page_titles[0] if page_titles else None,
    )

    for dir_key, files in buckets.items():
        if not dir_key:
            # Files at repo root go to overview
            if overview_title:
                result[overview_title].extend(files)
            continue

        target = _best_matching_page(dir_key, page_tokens, files)
        if target is None:
            if overview_title:
                result[overview_title].extend(files)
            continue

        # If the target would exceed the cap, try to split across all pages
        # whose tokens overlap this directory using remaining-capacity filling.
        if len(result[target]) + len(files) > MAX_FILES_PER_PAGE:
            matching_pages = [
                t for t in page_titles if _tokenize(dir_key) & page_tokens[t]
            ]
            remaining_capacity = {
                title: max(0, MAX_FILES_PER_PAGE - len(result[title]))
                for title in matching_pages
            }
            if len(matching_pages) > 1 and sum(remaining_capacity.values()) >= len(
                files
            ):
                file_iter = iter(files)
                try:
                    for title in matching_pages:
                        for _ in range(remaining_capacity[title]):
                            result[title].append(next(file_iter))
                except StopIteration:
                    pass
                continue

        result[target].extend(files)

    return result


def _heuristic_select_files(
    outline: list[dict],
    all_files: list[str],
    file_infos: dict[str, Any],
    dep_graph: DependencyGraph | None,
    partial_selections: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Score-based file selection used as Phase 2 fallback.

    Preserves valid partial selections from a failed LLM attempt, then fills
    remaining pages by pre-filtering to ~25 candidates and selecting the
    top-scoring files up to MAX_FILES_PER_PAGE.
    """
    result: dict[str, list[str]] = {}

    if partial_selections:
        for page in outline:
            title = page["title"]
            files = partial_selections.get(title, [])
            if 0 < len(files) <= MAX_FILES_PER_PAGE:
                result[title] = list(files)

    for page in outline:
        title = page["title"]
        if title in result:
            continue
        candidates = _prefilter_candidates(
            page, all_files, file_infos, dep_graph, max_candidates=25
        )
        if not candidates:
            result[title] = []
            continue
        scored = sorted(
            candidates,
            key=lambda f: _score_file_for_page(f, page, file_infos, dep_graph),
            reverse=True,
        )
        target = min(MAX_FILES_PER_PAGE, max(MIN_FILES_PER_PAGE, len(scored)))
        result[title] = scored[:target]

    for p in outline:
        result.setdefault(p["title"], [])

    return result


_PAGE_BATCH_SIZE = 12  # pages per LLM selection call


async def _select_files_in_batches(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    file_infos: dict[str, Any],
    dep_graph: DependencyGraph | None,
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    last_error: str | None = None,
) -> dict[str, list[str]]:
    """Phase 2: Ask the LLM to select representative files for each page.

    Pages are batched (_PAGE_BATCH_SIZE at a time).  Each page receives a
    pre-filtered candidate list (~25 files) so the LLM works with focused
    context.  The first batch runs serially to warm the Anthropic prompt cache;
    remaining batches run in parallel.
    """
    import asyncio

    all_files_set = set(all_files)
    valid_titles = [p["title"] for p in outline]
    result: dict[str, list[str]] = {t: [] for t in valid_titles}

    stage_system_seg = PromptSegment(text=system, cacheable=False)
    context_segs = _build_selection_system(file_summary, dep_info)
    system_segs: list[PromptSegment] = [stage_system_seg, *context_segs]

    async def _run_page_batch(batch_pages: list[dict]) -> None:
        pages_with_candidates = [
            (
                p["title"],
                p.get("purpose", ""),
                _prefilter_candidates(p, all_files, file_infos, dep_graph),
            )
            for p in batch_pages
        ]
        user_seg = _build_selection_user(pages_with_candidates, last_error)
        try:
            raw = await async_retry(
                llm.generate_structured,
                [user_seg],
                schema=_SELECTION_SCHEMA,
                system=system_segs,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
        except Exception as exc:
            log_validation_retry(
                logger,
                stage="wiki_planner.select_files.batch",
                attempt=1,
                max_retries=1,
                exc=exc,
                context={"batch_pages": len(batch_pages)},
            )
            return
        title_to_page = {p["title"]: p for p in batch_pages}
        candidate_files_by_title = {
            title: set(candidates)
            for title, _purpose, candidates in pages_with_candidates
        }
        for sel in raw.get("selections", []):
            title = sel.get("page_title", "")
            files = sel.get("files", [])
            if title not in title_to_page:
                continue
            valid = [
                f
                for f in files
                if f in all_files_set and f in candidate_files_by_title[title]
            ]
            page_dict = title_to_page[title]
            valid.sort(
                key=lambda f: _score_file_for_page(f, page_dict, file_infos, dep_graph),
                reverse=True,
            )
            result[title] = valid[:MAX_FILES_PER_PAGE]

    batches: list[list[dict]] = [
        outline[i : i + _PAGE_BATCH_SIZE]
        for i in range(0, len(outline), _PAGE_BATCH_SIZE)
    ]
    if batches:
        await _run_page_batch(batches[0])  # serial first batch warms cache
    if len(batches) > 1:
        await asyncio.gather(*(_run_page_batch(b) for b in batches[1:]))

    return result


async def _select_files(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    file_infos: dict[str, Any],
    dep_graph: DependencyGraph | None,
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    max_retries: int = 3,
    fast_llm: LLMProvider | None = None,
) -> dict[str, list[str]]:
    """Phase 2: Select representative files for each page with retry + feedback."""
    preferred_llm = fast_llm or llm
    last_error: str | None = None
    last_result: dict[str, list[str]] | None = None

    for attempt in range(1, max_retries + 1):
        current_llm = preferred_llm if attempt == 1 else llm
        try:
            result = await _select_files_in_batches(
                outline=outline,
                file_summary=file_summary,
                dep_info=dep_info,
                all_files=all_files,
                file_infos=file_infos,
                dep_graph=dep_graph,
                llm=current_llm,
                system=system,
                on_retry=on_retry,
                last_error=last_error,
            )
            last_result = result
            _validate_selections(result, outline)
            return result
        except ValueError as exc:
            last_error = str(exc)
            if attempt < max_retries:
                log_validation_retry(
                    logger,
                    stage="wiki_planner.select_files",
                    attempt=attempt,
                    max_retries=max_retries,
                    exc=exc,
                    context={"outline_pages": len(outline)},
                )
            else:
                log_final_failure(
                    logger,
                    stage="wiki_planner.select_files",
                    exc=exc,
                    context={"outline_pages": len(outline)},
                )

    raise ValueError(
        f"Failed to select files after {max_retries} attempts",
        last_error,
        last_result,
    )


def validate_wiki_plan(
    raw: dict,
    all_files: list[str] | None = None,
    existing_titles: set[str] | None = None,
    clusters: list[list[str]] | None = None,
    page_range: tuple[int, int] | None = None,
) -> WikiPlan:
    """Validate an LLM-produced wiki plan dict and return a :class:`WikiPlan`.

    Performs the following checks and normalisations:

    1. Raises :exc:`ValueError` if ``"pages"`` key is missing or empty.
    2. Raises :exc:`ValueError` if any page is missing ``"title"`` or
       ``"purpose"``.
    3. Raises :exc:`ValueError` if two or more pages produce the same slug
       (duplicate titles after slug derivation).
    4. Silently drops ``parent`` references that point to unknown titles
       rather than raising an error, to tolerate minor LLM hallucinations.
    5. Logs unselected files as info (selection model: pages curate a relevant
       subset of files; not all files need to appear on a page).

    Args:
        raw: Raw dict decoded from the LLM's JSON response.  Must contain a
            ``"pages"`` key whose value is a list of page dicts.
        all_files: Optional list of all relative file paths in the repository.
            When provided, files not selected by any page are logged at INFO.
        existing_titles: Optional set of page titles from the *unchanged*
            portion of an existing wiki plan (used during partial incremental
            refresh so cross-slice ``parent`` references remain valid).

    Returns:
        WikiPlan: A validated and normalised :class:`WikiPlan` instance.

    Raises:
        ValueError: If ``"pages"`` key is missing, the pages list is empty,
            any page dict is missing ``"title"`` or ``"purpose"``, or two or
            more pages share the same derived slug.

    Example:
        Normal case — all files assigned:

        >>> raw = {"pages": [
        ...     {"title": "Overview", "purpose": "Top level.", "files": ["main.py"]},
        ... ]}
        >>> plan = validate_wiki_plan(raw, all_files=["main.py"])
        >>> plan.pages[0].title
        'Overview'
    """
    if "pages" not in raw:
        raise ValueError("Missing 'pages' key")
    if not raw["pages"]:
        raise ValueError("Page plan must have at least one page")

    pages = []
    titles = {p["title"] for p in raw["pages"] if "title" in p}
    # Titles valid as parent references: new pages + any unchanged pages passed in
    all_known_titles = titles | (existing_titles or set())

    # Detect duplicate slugs before building the plan
    slug_counts: dict[str, int] = {}
    for p in raw["pages"]:
        if "title" in p:
            slug = _slugify_title(p["title"])
            slug_counts[slug] = slug_counts.get(slug, 0) + 1
    dupes = [slug for slug, count in slug_counts.items() if count > 1]
    if dupes:
        raise ValueError(f"Duplicate page slugs detected: {', '.join(dupes)}")

    for p in raw["pages"]:
        if "title" not in p:
            raise ValueError(f"Page missing 'title': {p}")
        if "purpose" not in p:
            raise ValueError(f"Page missing 'purpose': {p}")
        parent = p.get("parent")
        # Validate parent references a known title (new or unchanged)
        if parent and parent not in all_known_titles:
            parent = None  # Drop invalid parent rather than failing
        pages.append(
            WikiPageSpec(
                title=p["title"],
                purpose=p["purpose"],
                parent=parent,
                files=p.get("files", []),
            )
        )

    # ── Semantic validation ──────────────────────────────────────────────

    # Log unselected files (selection model: not all files need to be on a page)
    if all_files:
        selected = {f for page in pages for f in page.files}
        unselected = [f for f in all_files if f not in selected]
        if unselected:
            logger.info(
                "%d files not selected by any page (expected in selection model)",
                len(unselected),
            )

    # Max files per page
    for p in pages:
        if len(p.files) > MAX_FILES_PER_PAGE:
            sample = p.files[:3]
            raise ValueError(
                f"VALIDATION_FAILURE: Page '{p.title}' is overloaded "
                f"({len(p.files)} > {MAX_FILES_PER_PAGE} files). "
                f"Example files: {sample}. "
                "Suggestion: Split into 2-3 more focused sub-pages."
            )

    # No empty non-overview pages unless they are parents
    all_parents = {p.parent for p in pages if p.parent}
    for p in pages:
        is_overview = "overview" in p.title.lower()
        is_parent = p.title in all_parents
        if not (is_overview or is_parent or len(p.files) > 0):
            raise ValueError(
                f"VALIDATION_FAILURE: Page '{p.title}' has no files assigned. "
                "Every page must either own source files or serve as a parent "
                "category for other pages. Suggestion: Assign relevant files "
                "or remove this page."
            )

    # Hierarchy depth check
    title_to_parent = {p.title: p.parent for p in pages}

    def _depth(title: str) -> int:
        d = 1
        current = title
        seen: set[str] = {title}
        while title_to_parent.get(current) is not None:
            current = title_to_parent[current]
            if current in seen:
                raise ValueError(
                    f"Wiki hierarchy contains a parent cycle involving '{current}'"
                )
            seen.add(current)
            d += 1
        return d

    max_depth = max((_depth(p.title) for p in pages), default=1)
    if max_depth > 2:
        raise ValueError(
            f"Wiki hierarchy is {max_depth} levels deep — use exactly 2 levels: "
            "top-level pages for major subsystems, child pages for specific topics"
        )
    if max_depth == 1 and len(pages) > 1:
        raise ValueError(
            "All pages are top-level — use exactly 2 levels: top-level subsystem "
            "pages with child topic pages"
        )
    top_level = [p for p in pages if p.parent is None]
    if len(top_level) == 1 and len(pages) > 2:
        raise ValueError(
            "Single-root wiki plans are not allowed — top-level pages must be "
            "coherent subsystems, not a catch-all container"
        )

    # Page count vs suggested range
    if page_range is not None and len(pages) < page_range[0]:
        raise ValueError(
            f"Plan has {len(pages)} pages but minimum is {page_range[0]} — "
            "create more granular pages"
        )

    # Cluster coherence warning (not a rejection)
    if clusters:
        # Pre-build file→page mapping once (O(pages × files_per_page))
        # to avoid O(clusters × files × pages × files_per_page) nested loops.
        file_to_page: dict[str, str] = {}
        for p in pages:
            for f in p.files:
                file_to_page[f] = p.title
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            page_titles_for_cluster: set[str] = set()
            for f in cluster:
                if f in file_to_page:
                    page_titles_for_cluster.add(file_to_page[f])
            if len(page_titles_for_cluster) > 3:
                logger.warning(
                    "Cluster files [%s...] scattered across %d pages: %s",
                    cluster[0],
                    len(page_titles_for_cluster),
                    ", ".join(sorted(page_titles_for_cluster)),
                )

    return WikiPlan(pages=pages)


async def generate_wiki_plan(
    file_analysis: FileAnalysis,
    repo_name: str,
    llm: LLMProvider,
    dep_graph: DependencyGraph | None = None,
    max_retries: int = 3,
    readme: str | None = None,
    on_retry: OnRetryCallback | None = None,
    existing_titles: set[str] | None = None,
    wiki_language: str = "en",
    fast_llm: LLMProvider | None = None,
    user_steering: UserSteering | None = None,
    clone_root: Path | None = None,
) -> WikiPlan:
    """Generate a hierarchical wiki plan using two-phase LLM planning.

    Each phase validates its own output and self-retries up to *max_retries*
    times before surfacing an error, so problems are corrected as early as
    possible:

    * **Phase 1** (:func:`_generate_outline`) — Produces the page hierarchy
      (titles, purposes, parent relationships) and immediately validates
      structural constraints. Failure here is critical and stops the task.

    * **Phase 2** (:func:`_select_files`) — Selects representative source files
      for each page (5–8 target, 3–10 range) and validates per-page constraints.
      Failure triggers heuristic recovery via :func:`_heuristic_select_files`.

    * **Final** — Combines outline + assignments into a :class:`WikiPlan`.
      If validation fails even after heuristic recovery, a
      :class:`WikiPlannerError` is raised to signal a terminal failure.
    """
    from worker.pipeline.dependency_graph import format_for_llm_prompt
    from worker.pipeline.user_steering import assign_by_modules

    file_summary = file_analysis.to_llm_summary(dep_graph=dep_graph, max_files=200)
    all_files = list(file_analysis.files.keys())
    dep_info = format_for_llm_prompt(dep_graph) if dep_graph is not None else None
    clusters = dep_graph.clusters if dep_graph is not None else None

    entity_count = sum(len(info.entities) for info in file_analysis.files.values())
    page_range = _suggest_page_range(len(all_files), entity_count)

    system = _SYSTEM + get_planner_language_instruction(wiki_language)

    # --- User steering: skip Phase 1 when user provides page list ---
    if user_steering is not None and user_steering.pages:
        repo_notes_dicts = [{"content": n} for n in (user_steering.repo_notes or [])]
        module_assignments, unassigned = assign_by_modules(
            user_steering.pages, all_files
        )
        pages = []
        for upage in user_steering.pages:
            assigned_files = module_assignments.get(upage.title, [])
            page_notes_dicts = [{"content": n} for n in (upage.page_notes or [])]
            pages.append(
                WikiPageSpec(
                    title=upage.title,
                    purpose=upage.purpose or f"Documentation for {upage.title}.",
                    parent=upage.parent,
                    files=assigned_files,
                    page_notes=(
                        page_notes_dicts if page_notes_dicts else [{"content": ""}]
                    ),
                )
            )

        if unassigned and pages:
            overview = next(
                (p for p in pages if p.title.lower() == "overview"), pages[0]
            )
            overview.files = [*overview.files, *unassigned]

        return WikiPlan(
            repo_notes=repo_notes_dicts if repo_notes_dicts else [{"content": ""}],
            pages=pages,
        )

    # Build architectural anchors when a clone_root is provided.
    anchors_block: str | None = None
    if clone_root is not None:
        from worker.pipeline.ast_analysis import _rank_files_by_importance
        from worker.pipeline.outline_anchors import (
            build_directory_tree,
            extract_package_docstrings,
            extract_readme_sections,
            format_anchors_for_prompt,
        )

        ranked = _rank_files_by_importance(
            list(file_analysis.files),
            file_analysis.files,
            dep_graph=dep_graph,
        )
        tree = build_directory_tree(list(file_analysis.files), max_depth=3)
        pkg_docs = await extract_package_docstrings(
            clone_root=clone_root,
            rel_paths=ranked,
            max_entries=25,
        )
        readme_sections = extract_readme_sections(readme)
        anchors_block = (
            format_anchors_for_prompt(
                directory_tree=tree,
                package_docstrings=pkg_docs,
                readme_sections=readme_sections,
            )
            or None
        )

    # Phase 1: Generate outline + validate structure
    try:
        outline = await _generate_outline(
            file_summary=file_summary,
            repo_name=repo_name,
            llm=llm,
            readme=readme,
            dep_info=dep_info,
            clusters=clusters,
            page_range=page_range,
            system=system,
            on_retry=on_retry,
            max_retries=max_retries,
            total_file_count=len(all_files),
            anchors_block=anchors_block,
        )
    except WikiPlannerError as exc:
        # Phase 1 failure is critical — stop the task
        logger.error("Phase 1 critical failure: %s", exc)
        raise
    except Exception as exc:
        # Unexpected errors also stop the task in Phase 1
        logger.error("Phase 1 unexpected failure: %s", exc)
        raise WikiPlannerError(f"Outline generation failed: {exc}") from exc

    # Phase 2: Select representative files for each page (fast_llm for selection task)
    try:
        primary_assignments = await _select_files(
            outline=outline,
            file_summary=file_summary,
            dep_info=dep_info,
            all_files=all_files,
            file_infos=file_analysis.files,
            dep_graph=dep_graph,
            llm=llm,
            system=system,
            on_retry=on_retry,
            max_retries=max_retries,
            fast_llm=fast_llm,
        )
    except Exception as exc:
        # Phase 2 failure — trigger heuristic recovery while keeping the outline.
        # If it's a ValueError with 3 args (msg, last_error, last_result),
        # extract the partial assignments.
        partial = None
        if isinstance(exc, ValueError) and len(exc.args) == 3:
            partial = exc.args[2]

        recovery_type = "partial heuristic" if partial else "full heuristic"
        log_final_failure(
            logger,
            stage="wiki_planner.select_files.recovery",
            exc=exc,
            context={
                "recovery_type": recovery_type,
                "outline_pages": len(outline),
            },
        )
        primary_assignments = _heuristic_select_files(
            outline, all_files, file_analysis.files, dep_graph, partial
        )

    # Final: combine and normalise (handles orphan files, safety-net checks)
    raw = {
        "pages": [
            {
                "title": p["title"],
                "purpose": p["purpose"],
                "parent": p.get("parent"),
                "files": primary_assignments.get(p["title"], []),
            }
            for p in outline
        ]
    }
    try:
        plan = validate_wiki_plan(
            raw,
            all_files=all_files,
            existing_titles=existing_titles,
            clusters=clusters,
            page_range=page_range,
        )
        plan.all_repo_files = list(all_files)
        return plan
    except ValueError as exc:
        logger.error("Final wiki plan validation failed after recovery: %s", exc)
        # If even recovery fails, we MUST throw so the task terminates with an error.
        raise WikiPlannerError(f"Failed to build a valid wiki plan: {exc}") from exc
