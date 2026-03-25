from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from worker.llm.base import LLMProvider


@dataclass
class PageSpec:
    title: str
    slug: str
    modules: list[str]
    parent_slug: str | None = None
    description: str | None = None


@dataclass
class PagePlan:
    pages: list[PageSpec]


_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "slug": {"type": "string"},
                    "modules": {"type": "array", "items": {"type": "string"}},
                    "parent_slug": {"type": ["string", "null"]},
                    "description": {"type": "string"},
                },
                "required": ["title", "slug", "modules", "description"],
            },
        }
    },
    "required": ["pages"],
}

_SYSTEM = (
    "You are a senior technical documentation architect "
    "creating a comprehensive wiki structure for a software "
    "repository. You analyze codebases deeply — examining "
    "module structure, dependency relationships, and key "
    "entities — to produce a well-organized hierarchical "
    "wiki plan that helps developers understand the project "
    "quickly.\n\n"
    "Think step-by-step:\n"
    "1. Read the README to understand the project's purpose "
    "and architecture\n"
    "2. Examine the module tree and entity summaries to "
    "identify major components\n"
    "3. Use the dependency graph to understand how components "
    "relate to each other\n"
    "4. Group tightly-coupled modules into coherent pages\n"
    "5. Create a clear hierarchy: Overview -> subsystem "
    "pages -> detail pages\n\n"
    "Each page should have a clear PURPOSE — it should "
    "explain a concept, component, or workflow, not just "
    "list files. Write a concise but informative description "
    "for each page.\n\n"
    "Output ONLY valid JSON."
)


def _build_prompt(
    module_tree: list[dict],
    repo_name: str,
    readme: str | None = None,
    dep_summary: dict | None = None,
    clusters: list[list[str]] | None = None,
) -> str:
    tree_str = json.dumps(module_tree, indent=2)

    sections = [f"Repository: {repo_name}"]

    if readme:
        sections.append(f"README (excerpt):\n{readme[:2000]}")

    sections.append(f"Module tree with entities:\n{tree_str}")

    if dep_summary:
        dep_lines = []
        for mod, info in dep_summary.items():
            deps_on = ", ".join(info.get("depends_on", [])) or "none"
            deps_by = ", ".join(info.get("depended_by", [])) or "none"
            ext = ", ".join(info.get("external_deps", [])[:5]) or "none"
            dep_lines.append(
                f"  {mod}: depends_on=[{deps_on}], "
                f"depended_by=[{deps_by}], "
                f"external=[{ext}]"
            )
        sections.append("Dependency graph:\n" + "\n".join(dep_lines))

    if clusters:
        cluster_strs = [
            f"  Cluster {i + 1}: {', '.join(c[:10])}"
            for i, c in enumerate(clusters[:8])
        ]
        sections.append(
            "Suggested clusters (files that import each other heavily):\n"
            + "\n".join(cluster_strs)
        )

    complexity = _estimate_complexity(module_tree)
    schema_json = json.dumps(_PLAN_SCHEMA, indent=2)
    sections.append(
        "Create a hierarchical wiki plan. Guidelines:\n"
        f"- 5-15 pages depending on repository complexity "
        f"({complexity} modules detected)\n"
        "- Each page MUST have: title (descriptive, "
        "concept-oriented), slug (url-safe, lowercase, "
        "hyphens), modules (list of paths from the tree), "
        "description (1-2 sentences explaining what the "
        "page covers and why a developer would read it), "
        "and optionally parent_slug for nesting\n"
        '- MUST include an "Overview" page as the root '
        "(parent_slug: null) covering the project's "
        "purpose, architecture, and how components fit "
        "together\n"
        "- Group related modules using the dependency "
        "clusters as a guide — tightly-coupled modules "
        "belong together\n"
        "- Create 2-3 levels of hierarchy: Overview -> "
        "subsystem pages -> detail pages\n"
        "- Page titles should describe "
        'concepts/components (e.g., "Authentication & '
        'Authorization", "Data Pipeline Architecture"), '
        "not just directory names\n"
        "- Description should explain WHAT the page covers "
        "and WHY it matters to a developer new to this "
        "codebase\n"
        "- For the Overview page, the description should "
        "summarize the entire project\n\n"
        "Output JSON matching this schema:\n"
        f"{schema_json}"
    )

    return "\n\n".join(sections)


def _estimate_complexity(module_tree: list[dict]) -> int:
    return len(module_tree)


def validate_page_plan(raw: dict[str, Any]) -> PagePlan:
    if "pages" not in raw:
        raise ValueError("Missing 'pages' key")
    if not raw["pages"]:
        raise ValueError("Page plan must have at least one page")
    pages = []
    for p in raw["pages"]:
        if "slug" not in p:
            raise ValueError(f"Page missing 'slug': {p}")
        if "title" not in p:
            raise ValueError(f"Page missing 'title': {p}")
        pages.append(
            PageSpec(
                title=p["title"],
                slug=re.sub(r"[^a-z0-9-]", "-", p["slug"].lower()),
                modules=p.get("modules", ["."]),
                parent_slug=p.get("parent_slug"),
                description=p.get("description"),
            )
        )
    return PagePlan(pages=pages)


async def generate_page_plan(
    module_tree: list[dict],
    repo_name: str,
    llm: LLMProvider,
    max_retries: int = 3,
    readme: str | None = None,
    dep_summary: dict | None = None,
    clusters: list[list[str]] | None = None,
) -> PagePlan:
    prompt = _build_prompt(module_tree, repo_name, readme, dep_summary, clusters)
    for attempt in range(max_retries):
        try:
            raw = await llm.generate_structured(
                prompt, schema=_PLAN_SCHEMA, system=_SYSTEM
            )
            return validate_page_plan(raw)
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."
    # Fallback: flat plan covering all modules
    return PagePlan(
        pages=[
            PageSpec(
                title="Overview",
                slug="overview",
                modules=["."],
                description=(
                    f"High-level overview of the "
                    f"{repo_name} project architecture "
                    f"and components."
                ),
            ),
            *[
                PageSpec(
                    title=m["path"].replace("/", " ").title(),
                    slug=m["path"].replace("/", "-"),
                    modules=[m["path"]],
                    description=f"Documentation for the {m['path']} module.",
                )
                for m in module_tree
                if m["path"] != "."
            ],
        ]
    )
