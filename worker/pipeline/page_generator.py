from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from worker.llm.base import LLMProvider
from worker.embedding.base import EmbeddingProvider
from worker.pipeline.rag_indexer import FAISSStore
from worker.pipeline.wiki_planner import PageSpec

_SYSTEM = """You are a senior technical writer creating comprehensive, production-quality \
wiki documentation for a software repository. Your goal is to help developers new to this \
codebase understand it quickly and thoroughly.

Rules:
- Every technical claim MUST be grounded in the provided source code — do not invent APIs, \
classes, or features not present in the code context
- After each major section or subsection, add a source annotation in italics citing where \
the information comes from: *Source: path/to/file.py:10-45*
- Include Mermaid diagrams where they aid understanding — use ```mermaid code blocks
- Choose diagram types that best fit the content:
  - flowchart TD for architecture/data flow
  - classDiagram for class relationships
  - sequenceDiagram for request/response flows
  - graph LR for dependency relationships
- Write for developers who are new to this codebase but experienced programmers
- Use precise technical language and include concrete code examples from the source
- Organize content from high-level concepts down to implementation details"""

@dataclass
class PageResult:
    slug: str
    title: str
    content: str  # Markdown


def _format_entity_details(entities: list[dict[str, Any]]) -> str:
    """Format entity details (classes, functions) for the prompt."""
    if not entities:
        return "No entity details available."
    lines = []
    for e in entities[:25]:  # Cap to avoid prompt bloat
        parts = [f"- **{e.get('type', 'unknown')}** `{e.get('name', '?')}`"]
        if e.get("signature"):
            parts.append(f"  Signature: `{e['signature']}`")
        if e.get("docstring"):
            doc = e["docstring"][:150]
            parts.append(f"  Doc: {doc}")
        if e.get("start_line") and e.get("file"):
            parts.append(f"  Location: {e['file']}:{e['start_line']}-{e.get('end_line', '?')}")
        lines.append("\n".join(parts))
    return "\n".join(lines)


def _format_context_chunks(context_chunks: list[dict]) -> str:
    """Format RAG chunks with file paths and line numbers."""
    if not context_chunks:
        return "No source code context available."
    sections = []
    for c in context_chunks:
        file_path = c.get("file", "unknown")
        start = c.get("start_line", 0)
        end = c.get("end_line", 0)
        entity = c.get("entity")

        header = f"File: {file_path}"
        if start and end:
            header += f" (lines {start}-{end})"
        if entity:
            header += f" [{entity}]"

        sections.append(f"{header}\n```\n{c.get('text', '')}\n```")
    return "\n\n---\n\n".join(sections)


def _build_page_prompt(
    spec: PageSpec,
    context_chunks: list[dict],
    repo_name: str,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
) -> str:
    context = _format_context_chunks(context_chunks)

    sections = [
        f"Repository: {repo_name}",
        f"Page: {spec.title}",
    ]

    if spec.description:
        sections.append(f"Purpose: {spec.description}")

    sections.append(f"Modules: {', '.join(spec.modules)}")

    # Dependency context
    if dep_info:
        deps_on = dep_info.get("depends_on", [])
        deps_by = dep_info.get("depended_by", [])
        ext_deps = dep_info.get("external_deps", [])
        dep_lines = []
        if deps_on:
            dep_lines.append(f"- Depends on: {', '.join(deps_on)}")
        if deps_by:
            dep_lines.append(f"- Depended on by: {', '.join(deps_by)}")
        if ext_deps:
            dep_lines.append(f"- External dependencies: {', '.join(ext_deps[:10])}")
        if dep_lines:
            sections.append("Dependencies:\n" + "\n".join(dep_lines))

    # Entity details
    if entity_details:
        sections.append(f"Key entities in these modules:\n{_format_entity_details(entity_details)}")

    sections.append(f"Relevant source code (with file paths and line numbers):\n{context}")

    is_overview = spec.slug == "overview" or "overview" in spec.title.lower()

    if is_overview:
        sections.append(f"""Write a comprehensive Overview wiki page for the "{repo_name}" project. Structure:

## Overview
What this project does, its primary use cases, and the problem it solves.

## Architecture
Include a Mermaid diagram (```mermaid flowchart) showing the major components and how they connect.
Describe the high-level architecture, service topology, and data flow.

## Key Components
For each major subsystem/module, provide a brief description of its role.
After each component description, cite the source: *Source: file.py:line-line*

## Getting Started
How a developer would begin working with this codebase (entry points, key files to read first).

## Technology Stack
Key frameworks, libraries, and tools used.

Output Markdown only.""")
    else:
        sections.append(f"""Write a comprehensive wiki page for "{spec.title}". Structure:

## Overview
Brief description of this component's role, purpose, and design rationale.

## Architecture
Include a Mermaid diagram if it helps explain relationships (class diagram, flowchart, or sequence diagram).
Only include a diagram if it adds genuine value — not every page needs one.

## Key Components
For each major class/function in this module:
- What it does and why it exists
- Its interface/signature
- Key implementation details
- Usage example from the codebase if available
After each subsection, cite the source: *Source: file.py:line-line*

## Dependencies & Interactions
How this module connects to other parts of the codebase.
What it depends on and what depends on it.

## Source Files
Table or list of all source files covered by this page with brief descriptions.

Output Markdown only.""")

    return "\n\n".join(sections)


async def generate_page(
    spec: PageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    top_k: int = 12,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
) -> PageResult:
    # Multi-query RAG: generate multiple semantic queries for better coverage
    queries = [f"{spec.title} {' '.join(spec.modules)}"]

    if spec.description:
        queries.append(spec.description)

    # Add entity names as queries for targeted retrieval
    if entity_details:
        entity_names = [e.get("name", "") for e in entity_details[:5] if e.get("name")]
        if entity_names:
            queries.append(" ".join(entity_names))

    # Embed all queries and do multi-search
    query_vecs = []
    for q in queries:
        vec = await embedding.embed(q)
        query_vecs.append(vec)

    if len(query_vecs) > 1:
        context_chunks = store.multi_search(query_vecs, k=top_k)
    else:
        context_chunks = store.search(query_vecs[0], k=top_k)

    prompt = _build_page_prompt(spec, context_chunks, repo_name, dep_info, entity_details)
    content = await llm.generate(prompt, system=_SYSTEM)

    return PageResult(slug=spec.slug, title=spec.title, content=content)
