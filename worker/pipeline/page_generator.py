from __future__ import annotations
from dataclasses import dataclass
from worker.llm.base import LLMProvider
from worker.embedding.base import EmbeddingProvider
from worker.pipeline.rag_indexer import FAISSStore
from worker.pipeline.wiki_planner import PageSpec

_SYSTEM = """You are a technical documentation writer. Write clear, accurate wiki pages
for software repositories. Use Markdown. Include code examples where relevant.
Ground your writing in the provided code context — do not invent APIs."""

@dataclass
class PageResult:
    slug: str
    title: str
    content: str  # Markdown

def _build_page_prompt(spec: PageSpec, context_chunks: list[dict], repo_name: str) -> str:
    context = "\n\n---\n\n".join(
        f"File: {c.get('file', 'unknown')}\n{c['text']}"
        for c in context_chunks
    )
    return f"""Repository: {repo_name}
Page title: {spec.title}
Modules covered: {', '.join(spec.modules)}

Relevant source code:
{context}

Write a comprehensive wiki page for "{spec.title}". Include:
- Overview paragraph
- Key classes/functions with descriptions
- Usage examples where relevant
- How this module interacts with others

Output Markdown only."""

async def generate_page(
    spec: PageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    top_k: int = 8,
) -> PageResult:
    # Retrieve relevant chunks using the page title as the query
    query_vec = await embedding.embed(f"{spec.title} {' '.join(spec.modules)}")
    context_chunks = store.search(query_vec, k=top_k)

    prompt = _build_page_prompt(spec, context_chunks, repo_name)
    content = await llm.generate(prompt, system=_SYSTEM)

    return PageResult(slug=spec.slug, title=spec.title, content=content)
