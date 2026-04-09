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
   to the prompt.
5. Falls back to a flat cluster-based plan if all retries are exhausted.

The plan is represented as a :class:`WikiPlan` (a list of
:class:`WikiPageSpec` objects) and can be serialised to three different JSON
shapes via :meth:`WikiPlan.to_wiki_json`, :meth:`WikiPlan.to_internal_json`,
and :meth:`WikiPlan.to_api_structure`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from worker.llm.base import LLMProvider
from worker.pipeline.language import get_planner_language_instruction
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry


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
    files: list[str] = field(default_factory=list)  # rel_paths assigned by LLM

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

        Returns:
            dict: A dictionary with key ``"pages"``, a list of dicts each
            containing ``"title"``, ``"slug"``, ``"parent_slug"``, and
            ``"description"``.

        Example:
            >>> plan.to_api_structure()
            {'pages': [{'title': 'Overview', 'slug': 'overview',
                        'parent_slug': None, 'description': 'Project overview.'}]}
        """
        return {
            "pages": [
                {
                    "title": p.title,
                    "slug": p.slug,
                    "parent_slug": p.parent_slug,
                    "description": p.purpose,
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

_ASSIGNMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "page_title": {"type": "string"},
                },
                "required": ["file", "page_title"],
            },
        }
    },
    "required": ["assignments"],
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
    "on semantic purpose, not directory structure\n"
    "5. Create a clear hierarchy: top-level pages for major "
    "subsystems, child pages for details\n\n"
    "Each page should have a clear PURPOSE — it should "
    "explain a concept, component, or workflow. Every source "
    "file must be assigned to exactly one page.\n\n"
    "Output ONLY valid JSON."
)


def _build_outline_prompt(
    file_summary: str,
    repo_name: str,
    readme: str | None = None,
    dep_info: str | None = None,
    clusters: list[list[str]] | None = None,
    page_range: tuple[int, int] = (5, 20),
) -> str:
    """Build the Phase 1 prompt: generate page tree without file assignments."""
    sections = [f"Repository: {repo_name}"]

    if readme:
        sections.append(f"README:\n{readme}")

    sections.append(f"File summaries:\n{file_summary}")

    if dep_info:
        sections.append(f"Dependency relationships:\n{dep_info}")

    if clusters:
        cluster_strs = [
            f"  Cluster {i + 1}: {', '.join(c)}" for i, c in enumerate(clusters[:30])
        ]
        if len(clusters) > 30:
            cluster_strs.append(f"  ... and {len(clusters) - 30} more clusters")
        sections.append(
            "File clusters (files that import each other):\n" + "\n".join(cluster_strs)
        )

    schema_json = json.dumps(_OUTLINE_SCHEMA, indent=2)
    min_pages, max_pages = page_range
    sections.append(
        "Create a hierarchical wiki plan. Guidelines:\n"
        f"- Create between {min_pages} and {max_pages} pages. Prefer more granular "
        "pages over broad ones — a focused page covering 3-5 related files is better "
        "than a sprawling page covering 15+. Each page should have a clear, single "
        "responsibility.\n"
        "- Each page MUST have: title (descriptive, concept-oriented) and "
        "purpose (1-2 sentences explaining WHAT the page covers and WHY a "
        "developer would read it)\n"
        "- Optionally set parent (title of parent page) for hierarchy\n"
        "- Group by semantic purpose, not directory structure\n"
        "- Create 2-3 levels of hierarchy for larger repos\n"
        "- Page titles should describe concepts/components, not directory names\n"
        "- Do NOT assign files to pages — just define the page structure\n\n"
        "Output JSON matching this schema:\n"
        f"{schema_json}"
    )

    return "\n\n".join(sections)


def _build_assignment_prompt(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None = None,
    all_files: list[str] | None = None,
) -> str:
    """Build the Phase 2 prompt: assign every file to exactly one page."""
    sections = []

    outline_str = json.dumps(outline, indent=2)
    sections.append(f"Wiki page structure:\n{outline_str}")
    sections.append(f"File summaries:\n{file_summary}")

    if dep_info:
        sections.append(f"Dependency relationships:\n{dep_info}")

    total = len(all_files) if all_files else 0
    schema_json = json.dumps(_ASSIGNMENT_SCHEMA, indent=2)
    sections.append(
        f"Assign ALL {total} source files to pages. Guidelines:\n"
        "- Every file must be assigned to exactly one page\n"
        "- Files that import each other should be on the same page when possible\n"
        "- Assign files based on semantic purpose, not directory structure\n"
        "- Each assignment must reference an existing page title exactly\n\n"
        "Output JSON matching this schema:\n"
        f"{schema_json}"
    )

    return "\n\n".join(sections)


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
) -> list[dict]:
    """Phase 1: Generate page tree without file assignments."""
    prompt = _build_outline_prompt(
        file_summary=file_summary,
        repo_name=repo_name,
        readme=readme,
        dep_info=dep_info,
        clusters=clusters,
        page_range=page_range,
    )

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
            return pages
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."

    raise ValueError("Failed to generate outline after all retries")


async def _assign_files(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    max_retries: int = 3,
) -> dict[str, list[str]]:
    """Phase 2: Assign every file to a page. Returns {page_title: [files]}."""
    prompt = _build_assignment_prompt(
        outline=outline,
        file_summary=file_summary,
        dep_info=dep_info,
        all_files=all_files,
    )

    valid_titles = {p["title"] for p in outline}
    first_title = outline[0]["title"]

    for attempt in range(max_retries):
        try:
            raw = await async_retry(
                llm.generate_structured,
                prompt,
                schema=_ASSIGNMENT_SCHEMA,
                system=system,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
            assignments = raw.get("assignments", [])
            result: dict[str, list[str]] = {p["title"]: [] for p in outline}
            assigned_files: set[str] = set()

            for a in assignments:
                f = a.get("file", "")
                title = a.get("page_title", "")
                if f in all_files and f not in assigned_files:
                    target = title if title in valid_titles else first_title
                    result[target].append(f)
                    assigned_files.add(f)

            # Assign any unassigned files to the first page
            for f in all_files:
                if f not in assigned_files:
                    result[first_title].append(f)

            return result

        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."

    # Fallback: round-robin distribution
    result = {p["title"]: [] for p in outline}
    titles = [p["title"] for p in outline]
    for i, f in enumerate(sorted(all_files)):
        result[titles[i % len(titles)]].append(f)
    return result


def validate_wiki_plan(
    raw: dict,
    all_files: list[str] | None = None,
    existing_titles: set[str] | None = None,
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
    5. Appends any *orphaned* files (not assigned to any page) to the
       ``"Overview"`` page, or to the first page if no Overview exists.

    Args:
        raw: Raw dict decoded from the LLM's JSON response.  Must contain a
            ``"pages"`` key whose value is a list of page dicts.
        all_files: Optional list of all relative file paths in the repository.
            When provided, any file not referenced by any page is treated as an
            orphan and appended to the first matching page.
        existing_titles: Optional set of page titles from the *unchanged*
            portion of an existing wiki plan (used during partial incremental
            refresh so cross-slice ``parent`` references remain valid).

    Returns:
        WikiPlan: A validated and normalised :class:`WikiPlan` instance.

    Raises:
        ValueError: If ``"pages"`` key is missing, the pages list is empty,
            any page dict is missing ``"title"`` or ``"purpose"``, or two
            or more pages share the same derived slug.

    Example:
        Normal case — all files assigned, no orphans:

        >>> raw = {"pages": [
        ...     {"title": "Overview", "purpose": "Top level.", "files": ["main.py"]},
        ... ]}
        >>> plan = validate_wiki_plan(raw, all_files=["main.py"])
        >>> plan.pages[0].title
        'Overview'

        Orphan case — ``utils.py`` not assigned; it gets appended to Overview:

        >>> raw = {"pages": [
        ...     {"title": "Overview", "purpose": "...", "files": ["main.py"]},
        ... ]}
        >>> plan = validate_wiki_plan(raw, all_files=["main.py", "utils.py"])
        >>> "utils.py" in plan.pages[0].files
        True
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

    # Fix orphaned files: any file not assigned to any page goes to Overview
    if all_files:
        assigned = {f for page in pages for f in page.files}
        orphans = [f for f in all_files if f not in assigned]
        if orphans:
            # Find overview page or use first page
            overview = next(
                (p for p in pages if p.title.lower() == "overview"),
                pages[0] if pages else None,
            )
            if overview:
                overview.files = list(overview.files) + orphans

    return WikiPlan(pages=pages)


async def generate_wiki_plan(
    file_analysis,
    repo_name: str,
    llm: LLMProvider,
    dep_graph=None,
    max_retries: int = 3,
    readme: str | None = None,
    on_retry: OnRetryCallback | None = None,
    existing_titles: set[str] | None = None,
    wiki_language: str = "en",
) -> WikiPlan:
    """Generate a hierarchical wiki plan using two-phase LLM planning."""
    from worker.pipeline.dependency_graph import format_for_llm_prompt

    file_summary = file_analysis.to_llm_summary(dep_graph=dep_graph)
    all_files = list(file_analysis.files.keys())
    dep_info = format_for_llm_prompt(dep_graph) if dep_graph is not None else None
    clusters = dep_graph.clusters if dep_graph is not None else None

    # Compute entity count for page range heuristic
    entity_count = sum(len(info.entities) for info in file_analysis.files.values())
    page_range = _suggest_page_range(len(all_files), entity_count)

    system = _SYSTEM + get_planner_language_instruction(wiki_language)

    # Phase 1: Generate outline
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
        )
    except ValueError:
        # Fallback to cluster-based plan
        return _fallback_plan(repo_name, all_files, clusters)

    # Phase 2: Assign files to pages
    file_assignments = await _assign_files(
        outline=outline,
        file_summary=file_summary,
        dep_info=dep_info,
        all_files=all_files,
        llm=llm,
        system=system,
        on_retry=on_retry,
        max_retries=max_retries,
    )

    # Merge outline + assignments into WikiPlan
    pages = []
    for p in outline:
        title = p["title"]
        parent = p.get("parent")
        # Validate parent against known titles
        all_known = {pp["title"] for pp in outline} | (existing_titles or set())
        if parent and parent not in all_known:
            parent = None
        pages.append(
            WikiPageSpec(
                title=title,
                purpose=p["purpose"],
                parent=parent,
                files=file_assignments.get(title, []),
            )
        )

    return WikiPlan(pages=pages)


def _fallback_plan(
    repo_name: str,
    all_files: list[str],
    clusters: list[list[str]] | None,
) -> WikiPlan:
    """Build a flat cluster-based fallback plan when LLM planning fails."""
    fallback_pages = [
        WikiPageSpec(
            title="Overview",
            purpose=(
                f"High-level overview of the {repo_name} project "
                "architecture and components."
            ),
            files=[],
        )
    ]
    if clusters:
        assigned: set[str] = set()
        page_num = 1
        for cluster in clusters:
            for offset in range(0, max(1, len(cluster)), 20):
                chunk = cluster[offset : offset + 20]
                if not chunk:
                    continue
                suffix = f" (part {offset // 20 + 1})" if len(cluster) > 20 else ""
                fallback_pages.append(
                    WikiPageSpec(
                        title=f"Component {page_num}{suffix}",
                        purpose=f"Documentation for component {page_num}.",
                        files=chunk,
                    )
                )
                assigned.update(chunk)
            page_num += 1
        fallback_pages[0].files = [f for f in (all_files or []) if f not in assigned]
    else:
        fallback_pages[0].files = list(all_files or [])
    return WikiPlan(pages=fallback_pages)
