from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from worker.llm.base import LLMProvider
from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry


@dataclass
class WikiPageSpec:
    title: str
    purpose: str  # replaces "description"
    parent: str | None = None  # parent page TITLE string (not slug)
    page_notes: list[dict] = field(default_factory=lambda: [{"content": ""}])
    files: list[str] = field(default_factory=list)  # rel_paths assigned by LLM

    @property
    def slug(self) -> str:
        """Derive URL slug from title."""
        return re.sub(r"[^a-z0-9-]+", "-", self.title.lower()).strip("-")

    @property
    def parent_slug(self) -> str | None:
        if self.parent is None:
            return None
        return re.sub(r"[^a-z0-9-]+", "-", self.parent.lower()).strip("-")


@dataclass
class WikiPlan:
    repo_notes: list[dict] = field(default_factory=lambda: [{"content": ""}])
    pages: list[WikiPageSpec] = field(default_factory=list)

    def to_wiki_json(self) -> dict:
        """User-facing wiki.json: no slugs, no files field."""
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
        """Pipeline-internal: includes files for incremental refresh."""
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
        """API-compatible: slug/parent_slug/description for frontend."""
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


def validate_wiki_plan(raw: dict, all_files: list[str] | None = None) -> WikiPlan:
    """Validate LLM output and return WikiPlan. Fixes orphaned files."""
    if "pages" not in raw:
        raise ValueError("Missing 'pages' key")
    if not raw["pages"]:
        raise ValueError("Page plan must have at least one page")

    pages = []
    titles = {p["title"] for p in raw["pages"] if "title" in p}

    for p in raw["pages"]:
        if "title" not in p:
            raise ValueError(f"Page missing 'title': {p}")
        if "purpose" not in p:
            raise ValueError(f"Page missing 'purpose': {p}")
        parent = p.get("parent")
        # Validate parent references an existing title
        if parent and parent not in titles:
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
) -> WikiPlan:
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

    for attempt in range(max_retries):
        try:
            raw = await async_retry(
                llm.generate_structured,
                prompt,
                schema=_WIKI_PLAN_SCHEMA,
                system=_SYSTEM,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
            return validate_wiki_plan(raw, all_files=all_files)
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."

    # Fallback: flat plan - Overview + one page per dependency cluster
    fallback_pages = [
        WikiPageSpec(
            title="Overview",
            purpose=(
                f"High-level overview of the {repo_name} project "
                "architecture and components."
            ),
            files=all_files[:5] if all_files else [],
        )
    ]
    if clusters:
        for i, cluster in enumerate(clusters[:10]):
            if cluster:
                fallback_pages.append(
                    WikiPageSpec(
                        title=f"Component {i + 1}",
                        purpose=f"Documentation for component {i + 1}.",
                        files=cluster[:20],
                    )
                )
    elif all_files:
        # No clusters: put remaining files in overview
        if fallback_pages:
            fallback_pages[0].files = all_files
    return WikiPlan(pages=fallback_pages)
