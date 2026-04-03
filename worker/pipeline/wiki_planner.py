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

import json
import re
from dataclasses import dataclass, field

from worker.llm.base import LLMProvider
from worker.pipeline.language import get_planner_language_instruction
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry


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

        Returns:
            str: A URL-safe slug suitable for use as a filesystem name and
            URL path segment.

        Example:
            >>> WikiPageSpec(title="API Gateway", purpose="...").slug
            'api-gateway'
            >>> WikiPageSpec(title="  Worker / Job Queue  ", purpose="...").slug
            'worker-job-queue'
        """
        return re.sub(r"[^a-z0-9-]+", "-", self.title.lower()).strip("-")

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
            >>> WikiPageSpec(title="Overview", purpose="...").parent_slug is None
            True
        """
        if self.parent is None:
            return None
        return re.sub(r"[^a-z0-9-]+", "-", self.parent.lower()).strip("-")


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


_WIKI_PLAN_SCHEMA = {
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
                    "files": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "purpose", "files"],
            },
        }
    },
    "required": ["pages"],
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


def _build_prompt(
    file_summary: str,
    repo_name: str,
    readme: str | None = None,
    dep_info: str | None = None,
    clusters: list[list[str]] | None = None,
    all_files: list[str] | None = None,
) -> str:
    """Assemble the full LLM prompt for the wiki-planning step.

    Builds a multi-section prompt by concatenating available information
    about the repository.  Each optional section is only appended when the
    corresponding argument is not ``None``:

    * **README section** — Provides a human-written project overview; limited
      to the first 2000 characters to avoid exceeding context limits.
    * **File summaries section** — The text produced by
      ``FileAnalysis.to_llm_summary()``, listing each file with its detected
      entities and docstrings.
    * **Dependency relationships section** — The formatted dependency graph
      text, showing which files import which.
    * **Cluster suggestions section** — Up to 8 import-graph clusters (up to
      10 files each shown) as hints for grouping related files.
    * **Planning guidelines section** — Instructions and the JSON schema the
      LLM must conform to.

    Args:
        file_summary: Pre-formatted text output of
            ``FileAnalysis.to_llm_summary()``.
        repo_name: Human-readable repository name (e.g. ``"owner/repo"``).
        readme: Optional README content (truncated internally to 2000 chars).
        dep_info: Optional pre-formatted dependency graph text.
        clusters: Optional list of file-path lists representing import-graph
            clusters detected by the dependency analysis stage.
        all_files: Optional list of all relative file paths; used to embed
            the exact file count in the planning guidelines so the LLM knows
            it must cover every file.

    Returns:
        str: The full prompt string, with sections separated by blank lines.
    """
    sections = [f"Repository: {repo_name}"]

    if readme:
        sections.append(f"README (excerpt):\n{readme[:2000]}")

    sections.append(f"File summaries:\n{file_summary}")

    if dep_info:
        sections.append(f"Dependency relationships:\n{dep_info}")

    if clusters:
        cluster_strs = [
            f"  Cluster {i + 1}: {', '.join(c[:10])}"
            for i, c in enumerate(clusters[:8])
        ]
        sections.append(
            "Suggested clusters (files that import each other):\n"
            + "\n".join(cluster_strs)
        )

    total_files = len(all_files) if all_files else 0
    schema_json = json.dumps(_WIKI_PLAN_SCHEMA, indent=2)
    sections.append(
        "Create a hierarchical wiki plan. Guidelines:\n"
        f"- Assign ALL {total_files} source files to pages\n"
        "- Each page MUST have: title (descriptive, concept-oriented), "
        "purpose (1-2 sentences), files (list of rel_paths from the "
        "file summaries), and optionally parent (title of parent page)\n"
        "- Group files by semantic purpose, not by directory structure — "
        "files from different directories may belong on the same page\n"
        "- Create 2-3 levels of hierarchy for larger repos\n"
        "- Page titles should describe concepts/components, not directory names\n"
        "- The purpose should explain WHAT the page covers and WHY a "
        "developer would read it\n\n"
        "Output JSON matching this schema:\n"
        f"{schema_json}"
    )

    return "\n\n".join(sections)


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
            slug = re.sub(r"[^a-z0-9-]+", "-", p["title"].lower()).strip("-")
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
    file_analysis,  # FileAnalysis from ast_analysis
    repo_name: str,
    llm: LLMProvider,
    dep_graph=None,  # DependencyGraph from dependency_graph (optional)
    max_retries: int = 3,
    readme: str | None = None,
    on_retry: OnRetryCallback | None = None,
    existing_titles: set[str] | None = None,
    wiki_language: str = "en",
) -> WikiPlan:
    """Generate a hierarchical wiki plan for a repository using an LLM.

    Orchestrates the full planning workflow:

    1. Converts *file_analysis* to an LLM-readable text summary.
    2. Formats dependency info and cluster hints from *dep_graph* (if given).
    3. Builds the prompt via :func:`_build_prompt`.
    4. Calls ``llm.generate_structured`` with ``_WIKI_PLAN_SCHEMA`` inside an
       ``async_retry`` wrapper (for transient API errors).
    5. Validates the LLM output with :func:`validate_wiki_plan`; if validation
       raises, appends the error to the prompt and retries up to *max_retries*
       times in total.
    6. If all retries are exhausted, falls back to a flat plan: one
       ``"Overview"`` page plus one ``"Component N"`` page per import-graph
       cluster (clusters are split into sub-pages of at most 20 files).  All
       files not placed in a cluster page are routed to Overview.

    Args:
        file_analysis: A :class:`~worker.pipeline.ast_analysis.FileAnalysis`
            instance containing the per-file entity data for the repository.
        repo_name: Human-readable repository name used in prompts and fallback
            page purposes (e.g. ``"owner/repo"``).
        llm: An :class:`~worker.llm.base.LLMProvider` instance used to call
            the LLM for structured JSON generation.
        dep_graph: Optional
            :class:`~worker.pipeline.dependency_graph.DependencyGraph`
            providing import relationships and cluster suggestions.
        max_retries: Maximum number of LLM call + validation attempts before
            the fallback plan is used.  Defaults to ``3``.
        readme: Optional README text extracted by
            :func:`~worker.pipeline.ingestion.extract_readme`; included in the
            prompt when provided.
        on_retry: Optional callback passed to ``async_retry`` for progress
            reporting on transient embedding/LLM failures.
        existing_titles: Optional set of page titles from the *unchanged*
            portion of an existing wiki plan (for partial incremental refresh).
            Passed through to :func:`validate_wiki_plan`.

    Returns:
        WikiPlan: A validated :class:`WikiPlan` where every source file in
        *file_analysis* is assigned to exactly one page.

    Example:
        >>> plan = await generate_wiki_plan(
        ...     file_analysis=analysis,
        ...     repo_name="owner/my-repo",
        ...     llm=llm_provider,
        ...     dep_graph=dep_graph,
        ...     readme=readme_text,
        ... )
        >>> len(plan.pages)
        12
        >>> plan.pages[0].title
        'Overview'
    """
    from worker.pipeline.ast_analysis import FileAnalysis  # noqa: F401
    from worker.pipeline.dependency_graph import (  # noqa: F401
        DependencyGraph,
        format_for_llm_prompt,
    )

    file_summary = file_analysis.to_llm_summary()
    all_files = list(file_analysis.files.keys())
    dep_info = format_for_llm_prompt(dep_graph) if dep_graph is not None else None
    clusters = dep_graph.clusters if dep_graph is not None else None

    prompt = _build_prompt(
        file_summary=file_summary,
        repo_name=repo_name,
        readme=readme,
        dep_info=dep_info,
        clusters=clusters,
        all_files=all_files,
    )

    system = _SYSTEM + get_planner_language_instruction(wiki_language)
    for attempt in range(max_retries):
        try:
            raw = await async_retry(
                llm.generate_structured,
                prompt,
                schema=_WIKI_PLAN_SCHEMA,
                system=system,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
            return validate_wiki_plan(
                raw, all_files=all_files, existing_titles=existing_titles
            )
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."

    # Fallback: flat plan - Overview + one page per dependency cluster.
    # Every file must be assigned exactly once.
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
            # Split large clusters into pages of up to 20 files each
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
        # Route any file not placed in a cluster page to Overview
        fallback_pages[0].files = [f for f in (all_files or []) if f not in assigned]
    else:
        # No clusters: put all files in Overview
        fallback_pages[0].files = list(all_files or [])
    return WikiPlan(pages=fallback_pages)
