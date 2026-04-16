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
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

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
    files: list[str] = field(default_factory=list)  # primary rel_paths assigned by LLM
    secondary_files: list[str] = field(default_factory=list)
    """Files *referenced* by this page but *primarily owned* by another page.

    Included in the generation prompt as "see also" context and used by
    incremental refresh to mark the page as stale when one of them changes.
    """

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
                    "secondary_files": p.secondary_files,
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
                    "secondary_file_count": len(p.secondary_files),
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
    anchors_block: str | None = None,
) -> str:
    """Build the Phase 1 prompt: generate page tree without file assignments."""
    sections = [f"Repository: {repo_name}"]

    if readme:
        sections.append(f"README:\n{readme}")

    if anchors_block:
        sections.append("Architectural anchors:\n" + anchors_block)

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
                cluster_strs.append(
                    f"  Large cluster ({len(c)} files) — see dependency "
                    "relationships above for internal structure"
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


def _build_batch_assignment_system(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
) -> list[PromptSegment]:
    """Build the cacheable *system* portion of a batched assignment call.

    The system turn contains the full repository context — outline,
    file summaries, and dependency info — which is identical for every
    batch in a single planning run.  Marking it cacheable lets Anthropic's
    ``ephemeral`` cache amortise the tokens across batches so only the
    first batch pays full cost.

    Non-Anthropic providers (OpenAI, Gemini, Ollama) ignore the cache
    hint and simply concatenate the segments into the system prompt.
    """
    outline_json = json.dumps(outline, indent=2)
    parts: list[str] = [
        "You are assigning source files to wiki pages.",
        "",
        f"## Wiki page structure:\n{outline_json}",
        "",
        f"## File summaries:\n{file_summary}",
    ]
    if dep_info:
        parts.append("")
        parts.append(f"## Dependency relationships:\n{dep_info}")
    return [PromptSegment(text="\n".join(parts), cacheable=True)]


def _build_batch_assignment_user(
    batch_files: list[str],
    outline_titles: list[str],
) -> PromptSegment:
    """Build the per-batch *user* segment.

    Contains only the batch-specific content — the file list to assign and
    a reminder of valid page titles — so the system segment's cache stays
    valid across batches.
    """
    titles_str = ", ".join(f'"{t}"' for t in outline_titles)
    files_str = "\n".join(f"- {f}" for f in batch_files)
    schema_json = json.dumps(_ASSIGNMENT_SCHEMA, indent=2)
    text = (
        f"Assign each of the following {len(batch_files)} files to one of the "
        f"page titles below.  Each ``page_title`` MUST exactly match one of: "
        f"{titles_str}.\n\n"
        f"Files to assign:\n{files_str}\n\n"
        "Rules:\n"
        "- Every listed file must appear in the output.\n"
        "- Choose the page whose purpose best matches the file's semantic role.\n"
        "- Files that import each other usually belong on the same page.\n\n"
        f"Output JSON matching this schema:\n{schema_json}"
    )
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
    - Hierarchy depth > 4.
    - Flat plan when the repository has > 30 files.
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
    if max_depth > 4:
        raise ValueError(
            f"Wiki hierarchy is {max_depth} levels deep — flatten to at most 4 levels"
        )
    if max_depth == 1 and total_file_count > 30:
        raise ValueError(
            f"All pages are top-level — create 2-3 levels of hierarchy "
            f"for a repo with {total_file_count} files"
        )
    if len(pages) < page_range[0]:
        raise ValueError(
            f"Plan has {len(pages)} pages but minimum is {page_range[0]} — "
            "create more granular pages"
        )


def _validate_assignments(
    result: dict[str, list[str]],
    outline: list[dict],
) -> None:
    """Validate per-page file assignment counts.

    Called inside :func:`_assign_files` after each LLM response so that
    assignment-level errors are caught and retried in Phase 2.

    Checks:
    - No page has more than 25 files (over-stuffed pages make poor wiki pages).
    - No non-overview page has zero files (empty pages add noise).

    Raises:
        ValueError: Describing the first constraint that is violated.
    """
    for page in outline:
        title = page["title"]
        files = result.get(title, [])
        if len(files) > 25:
            raise ValueError(
                f"Page '{title}' has {len(files)} files — "
                "split into focused sub-pages of ≤25 files each"
            )
        if "overview" not in title.lower() and len(files) == 0:
            raise ValueError(
                f"Page '{title}' has no files assigned — "
                "either assign files or remove it"
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
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."

    exc = ValueError("Failed to generate outline after all retries")
    log_final_failure(
        logger,
        stage="wiki_planner.outline",
        exc=exc,
        context={
            "total_files": total_file_count,
            "max_retries": max_retries,
        },
    )
    raise exc


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


def _directory_cluster_assign(
    outline: list[dict],
    all_files: list[str],
) -> dict[str, list[str]]:
    """Locality-preserving file assignment fallback.

    Groups files by their top-level directory, then assigns each directory
    group to the page whose title + purpose tokens best match the directory
    name and sample file basenames.  Unmatched files go to the "Overview"
    page if one exists, else to the first outline page.

    When a single page would receive more than 25 files (the per-page cap
    enforced by :func:`_validate_assignments`), the group is split across
    all pages whose tokens matched the directory at all.
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
        if len(result[target]) + len(files) > 25:
            matching_pages = [
                t for t in page_titles if _tokenize(dir_key) & page_tokens[t]
            ]
            remaining_capacity = {
                title: max(0, 25 - len(result[title])) for title in matching_pages
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


_BATCH_SIZE_DEFAULT = 40


async def _assign_files_in_batches(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    batch_size: int = _BATCH_SIZE_DEFAULT,
    max_cleanup_retries: int = 2,
) -> dict[str, list[str]]:
    """Phase 2 batched assignment core.

    Splits *all_files* into chunks of *batch_size* and invokes
    ``llm.generate_structured`` once per batch.  The system turn — which
    carries the large outline + file summary + dep info — is built once
    and reused across every call so Anthropic's ``ephemeral`` cache
    amortises the context tokens.

    Partial results are merged across batches.  Any files the LLM fails
    to assign are re-batched for up to *max_cleanup_retries* cleanup rounds
    before being handed off to directory clustering for the residue.
    """
    import asyncio

    valid_titles = [p["title"] for p in outline]
    valid_titles_set = set(valid_titles)
    result: dict[str, list[str]] = {t: [] for t in valid_titles}
    assigned: set[str] = set()

    # Build the cacheable system segment ONCE and reuse across all batches.
    stage_system_seg = PromptSegment(text=system, cacheable=False)
    context_segs = _build_batch_assignment_system(
        outline=outline,
        file_summary=file_summary,
        dep_info=dep_info,
    )
    system_segments: list[PromptSegment] = [stage_system_seg, *context_segs]

    async def _run_batch(batch: list[str]) -> None:
        user_segment = _build_batch_assignment_user(
            batch_files=batch,
            outline_titles=valid_titles,
        )
        try:
            raw = await async_retry(
                llm.generate_structured,
                user_segment,
                schema=_ASSIGNMENT_SCHEMA,
                system=system_segments,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
        except Exception as exc:
            log_validation_retry(
                logger,
                stage="wiki_planner.assign_files.batch",
                attempt=1,
                max_retries=1,
                exc=exc,
                context={"batch_size": len(batch)},
            )
            return
        for a in raw.get("assignments", []):
            f = a.get("file", "")
            title = a.get("page_title", "")
            if f not in batch or f in assigned:
                continue
            if title not in valid_titles_set:
                continue
            result[title].append(f)
            assigned.add(f)

    # Initial pass: batch every file.
    # Run the first batch serially to warm the cache, then the rest in parallel.
    batches: list[list[str]] = [
        all_files[i : i + batch_size] for i in range(0, len(all_files), batch_size)
    ]
    if batches:
        await _run_batch(batches[0])
    if len(batches) > 1:
        await asyncio.gather(*(_run_batch(b) for b in batches[1:]))

    # Cleanup rounds for unassigned files
    for attempt in range(1, max_cleanup_retries + 1):
        unassigned = [f for f in all_files if f not in assigned]
        if not unassigned:
            break
        log_validation_retry(
            logger,
            stage="wiki_planner.assign_files.cleanup",
            attempt=attempt,
            max_retries=max_cleanup_retries,
            exc=ValueError(f"{len(unassigned)} files unassigned after batches"),
            context={"unassigned": len(unassigned), "total": len(all_files)},
        )
        cleanup_batches = [
            unassigned[i : i + batch_size]
            for i in range(0, len(unassigned), batch_size)
        ]
        for b in cleanup_batches:
            await _run_batch(b)

    # Anything still unassigned → directory clustering residue
    unassigned = [f for f in all_files if f not in assigned]
    if unassigned:
        log_final_failure(
            logger,
            stage="wiki_planner.assign_files.residue",
            exc=ValueError(
                f"{len(unassigned)} files still unassigned after cleanup; "
                "routing residue to directory clustering"
            ),
            context={"residue": len(unassigned)},
        )
        residue_assignment = _directory_cluster_assign(outline, unassigned)
        for title, files in residue_assignment.items():
            result[title].extend(files)

    return result


async def _assign_files(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    max_retries: int = 3,
    _extra_context: str | None = None,
    fast_llm: LLMProvider | None = None,
) -> dict[str, list[str]]:
    """Phase 2: Assign every file to a page via batched LLM calls.

    Delegates to :func:`_assign_files_in_batches`, which splits the file
    list into ≤40-file batches and reuses a cacheable system segment
    across batches.  ``fast_llm`` is preferred for the batched call because
    classification-style assignments scale well on faster models.
    Validation is performed on the merged result, and validation failures
    trigger up to ``max_retries`` attempts (using ``fast_llm`` on the first
    attempt and ``llm`` for subsequent retries) before falling back to
    directory clustering.
    """
    preferred_llm = fast_llm or llm

    for attempt in range(1, max_retries + 1):
        current_llm = preferred_llm if attempt == 1 else llm
        try:
            result = await _assign_files_in_batches(
                outline=outline,
                file_summary=file_summary,
                dep_info=dep_info,
                all_files=all_files,
                llm=current_llm,
                system=system,
                on_retry=on_retry,
            )
            _validate_assignments(result, outline)
            return result
        except ValueError as exc:
            if attempt < max_retries:
                log_validation_retry(
                    logger,
                    stage="wiki_planner.assign_files",
                    attempt=attempt,
                    max_retries=max_retries,
                    exc=exc,
                    context={
                        "outline_pages": len(outline),
                        "total_files": len(all_files),
                    },
                )
            else:
                log_final_failure(
                    logger,
                    stage="wiki_planner.assign_files",
                    exc=exc,
                    context={
                        "outline_pages": len(outline),
                        "total_files": len(all_files),
                    },
                )

    # Final fallback: directory clustering (locality-preserving)
    return _directory_cluster_assign(outline, all_files)


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

    # ── Semantic validation ──────────────────────────────────────────────

    # Max files per page
    for p in pages:
        if len(p.files) > 25:
            raise ValueError(
                f"Page '{p.title}' has {len(p.files)} files — "
                "split into focused sub-pages of ≤25 files each"
            )

    # No empty non-overview pages
    for p in pages:
        is_overview = "overview" in p.title.lower()
        if not is_overview and len(p.files) == 0:
            raise ValueError(
                f"Page '{p.title}' has no files assigned — "
                "either assign files or remove it"
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
    if max_depth > 4:
        raise ValueError(
            f"Wiki hierarchy is {max_depth} levels deep — flatten to at most 4 levels"
        )

    # Flat plan check for repos with >30 files
    total_file_count = len(all_files) if all_files else sum(len(p.files) for p in pages)
    if max_depth == 1 and total_file_count > 30:
        raise ValueError(
            f"All pages are top-level — create 2-3 levels of "
            f"hierarchy for a repo with {total_file_count} files"
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
      structural constraints (duplicate slugs, cycles, depth, flat plan, page
      count) via :func:`_validate_outline_structure`.  A bad outline is
      re-generated within Phase 1 rather than being discovered after the
      expensive Phase 2 LLM call.

    * **Phase 2** (:func:`_assign_files`) — Assigns every source file to a
      page and immediately validates per-page constraints (over-stuffed pages,
      empty non-overview pages) via :func:`_validate_assignments`.  Bad
      assignments are re-generated within Phase 2.

    * **Final** — Combines outline + assignments into a :class:`WikiPlan` via
      :func:`validate_wiki_plan`, which handles orphan-file assignment as a
      normalisation step.  Any remaining error falls back to a cluster-based
      plan.
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
        pkg_docs = extract_package_docstrings(
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
    except ValueError:
        return _fallback_plan(repo_name, all_files, clusters)

    # Phase 2: Assign files + validate assignments (fast_llm for classification task)
    file_assignments = await _assign_files(
        outline=outline,
        file_summary=file_summary,
        dep_info=dep_info,
        all_files=all_files,
        llm=llm,
        system=system,
        on_retry=on_retry,
        max_retries=max_retries,
        fast_llm=fast_llm,
    )

    # Final: combine and normalise (handles orphan files, safety-net checks)
    raw = {
        "pages": [
            {
                "title": p["title"],
                "purpose": p["purpose"],
                "parent": p.get("parent"),
                "files": file_assignments.get(p["title"], []),
            }
            for p in outline
        ]
    }
    try:
        return validate_wiki_plan(
            raw,
            all_files=all_files,
            existing_titles=existing_titles,
            clusters=clusters,
            page_range=page_range,
        )
    except ValueError as exc:
        logger.warning("Final wiki plan validation failed: %s — using fallback", exc)
        return _fallback_plan(repo_name, all_files, clusters)


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
