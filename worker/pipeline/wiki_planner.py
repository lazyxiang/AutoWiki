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
                },
                "required": ["title", "slug", "modules"],
            },
        }
    },
    "required": ["pages"],
}

_SYSTEM = """You are a technical documentation architect. Given a repository's module tree,
produce a hierarchical wiki page plan. Each page covers one logical concern.
Output ONLY valid JSON."""


def _build_prompt(module_tree: list[dict], repo_name: str) -> str:
    tree_str = json.dumps(module_tree, indent=2)
    return f"""Repository: {repo_name}

Module tree:
{tree_str}

Create a wiki page plan. Guidelines:
- 3–10 pages total
- Each page has: title (human-readable), slug (url-safe, lowercase, hyphens), modules (list of paths from the tree)
- Include an "Overview" page covering the root
- Group related modules into logical pages
- Optionally set parent_slug for nested pages

Output JSON exactly matching this schema:
{json.dumps(_PLAN_SCHEMA, indent=2)}"""


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
        pages.append(PageSpec(
            title=p["title"],
            slug=re.sub(r"[^a-z0-9-]", "-", p["slug"].lower()),
            modules=p.get("modules", ["."]),
            parent_slug=p.get("parent_slug"),
        ))
    return PagePlan(pages=pages)


async def generate_page_plan(
    module_tree: list[dict],
    repo_name: str,
    llm: LLMProvider,
    max_retries: int = 3,
) -> PagePlan:
    prompt = _build_prompt(module_tree, repo_name)
    last_error = None
    for attempt in range(max_retries):
        try:
            raw = await llm.generate_structured(prompt, schema=_PLAN_SCHEMA, system=_SYSTEM)
            return validate_page_plan(raw)
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            last_error = e
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."
    # Fallback: flat plan covering all modules
    return PagePlan(pages=[
        PageSpec(title="Overview", slug="overview", modules=["."]),
        *[PageSpec(title=m["path"].replace("/", " ").title(), slug=m["path"].replace("/", "-"),
                   modules=[m["path"]]) for m in module_tree if m["path"] != "."],
    ])
