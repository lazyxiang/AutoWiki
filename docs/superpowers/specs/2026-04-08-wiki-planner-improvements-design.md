# Wiki Planner & Generation Pipeline Improvements

**Date**: 2026-04-08
**Status**: Implemented (branch `feature/wiki-planner-improvements`)
**Scope**: worker/llm, worker/pipeline, worker/jobs

## Implementation notes (deviations from spec)

- **`to_llm_summary` default**: spec said `max_files=0` (no limit, safety cap 800). Implemented as `max_files=200` so callers that omit the argument stay bounded. Pass `0` to explicitly opt in to the 800-file cap.
- **Dependency list truncation**: each file's internal and external import lists are capped at 10 entries with a `+N more` suffix to prevent hub files from dominating the prompt.
- **Stage 7 (diagram synthesis) removed**: `diagram_synthesis.py` was dropped entirely — the Overview page generator's prompt template already produces an architecture Mermaid diagram, making Stage 7 redundant. The pipeline is now 6 stages.
- **`--reuse-index` / `reuse_index`**: new bool param threaded from CLI → `IndexRequest` → `enqueue_full_index` → `run_full_index`. When true, existing FAISS files are preserved and Stage 4 is skipped.
- **Per-phase validation added**: `_validate_outline_structure()` fires immediately after Phase 1; `_validate_assignments()` fires immediately after Phase 2. This replaces the deferred Phase 3 retry loop and error-type classification helpers that were added mid-implementation but then superseded.
- **Importance-ranked file selection**: when the file count exceeds `max_files`, `_rank_files_by_importance()` selects the most architecturally significant files (scored by entity count, in-degree, entry-point name bonus, shallowness) rather than falling back to alphabetical order.

## Problem

AutoWiki's wiki planner produces shallow, imprecise plans compared to DeepWiki (14 pages vs ~30 for the same repo). The page generator treats all pages as independent — parent pages cannot synthesize child content, leading to duplication and shallow overviews. The planner's single LLM call is overloaded with both structural thinking and file assignment, and receives sparse file summaries that lack semantic context.

## Goals

1. Parent wiki pages synthesize child content rather than duplicating it (bottom-up multi-agent generation)
2. Wiki plans are more granular, with focused pages of 3–15 files each
3. File-to-page assignment is accurate — tightly coupled files land on the same page
4. The planner scales to repos of any size without arbitrary truncation

## Non-Goals

- Backward compatibility shims for old interfaces
- Cost optimization (accuracy takes precedence)
- Streaming/real-time plan updates to the frontend during planning

## Implementation Order

Each improvement builds on the previous:

1. `generate_batch` on LLMProvider
2. Richer file summaries
3. Dependency-aware grouping
4. Dynamic page count
5. Two-phase planning
6. Enhanced plan validation
7. Multi-agent bottom-up generation

---

## 1. `generate_batch` on LLMProvider

### File: `worker/llm/base.py`

Add to `LLMProvider` ABC:

```python
async def generate_batch(
    self,
    prompts: list[str],
    system: str = "",
    max_concurrency: int = 5,
) -> list[str]:
    """Generate responses for multiple prompts concurrently.

    Default implementation uses asyncio.gather with a semaphore.
    Providers may override to use native batch APIs.
    """
    sem = asyncio.Semaphore(max_concurrency)
    async def _one(prompt: str) -> str:
        async with sem:
            return await self.generate(prompt, system)
    return await asyncio.gather(*[_one(p) for p in prompts])
```

This is a concrete method on the ABC (not abstract) — all providers inherit it automatically. No changes needed to `anthropic_provider.py`, `openai_provider.py`, `gemini_provider.py`, or `ollama_provider.py`.

### File: `worker/llm/base.py` — LoggingLLMProvider

Add wrapper:

```python
async def generate_batch(
    self,
    prompts: list[str],
    system: str = "",
    max_concurrency: int = 5,
) -> list[str]:
    logger.debug("LLM REQUEST (batch): %d prompts, system=%s", len(prompts), _truncate(system))
    results = await self._provider.generate_batch(prompts, system, max_concurrency)
    logger.debug("LLM RESPONSE (batch): %d responses, total %d chars",
                 len(results), sum(len(r) for r in results))
    return results
```

---

## 2. Richer File Summaries

### File: `worker/pipeline/ast_analysis.py`

Change `FileAnalysis.to_llm_summary()` signature:

```python
def to_llm_summary(self, max_files: int = 0, dep_graph: DependencyGraph | None = None) -> str:
```

- `max_files=0` means no limit. Safety cap at 800 files — beyond that, omitted files are listed as bare paths (no entities/docs) so the planner still knows they exist.
- For each file, append up to two additional lines:
  - Import/external deps line: `  imports: mod_a, mod_b | external: fastapi, pydantic`
  - First top-level entity docstring (truncated to 120 chars): `  "FastAPI application lifecycle and startup configuration."`
- Files with no entities still show `(no named entities)` but get the import line if available.

### Output format example

```
api/main.py: 0 classes, 1 functions [lifespan]
  imports: shared.config, shared.database | external: fastapi
  "FastAPI application lifecycle and startup configuration."
worker/jobs.py: 0 classes, 12 functions [_update_job, _update_repo, ...]
  imports: shared.config, shared.database, worker.pipeline.ast_analysis, ... | external: sqlalchemy
  "ARQ job functions that orchestrate the 7-stage wiki generation pipeline."
tests/conftest.py: 0 classes, 3 functions [fixture_repo_path, mock_llm, mock_embedding]
  (no dependencies)
```

### Callers

Update `_build_prompt()` in `wiki_planner.py` and `run_full_index`/`run_refresh_index` in `jobs.py` to pass `dep_graph` when calling `to_llm_summary()`.

---

## 3. Dependency-Aware Grouping

### File: `worker/pipeline/dependency_graph.py`

Add function:

```python
def _split_large_cluster(
    cluster: list[str],
    edges: dict[str, list[str]],
    max_size: int = 15,
) -> list[list[str]]:
    """Split a large cluster into sub-clusters using BFS-seed grouping.

    Algorithm:
    1. Pick the file with the most import edges as the first seed.
    2. BFS outward through the sub-graph, adding files until max_size.
    3. Remaining unvisited files become the pool for the next seed.
    4. Repeat until all files are assigned.

    Returns list of sub-clusters, each sorted alphabetically.
    """
```

Modify `_compute_clusters()` to call `_split_large_cluster()` on any component exceeding `max_size=15`.

### File: `worker/pipeline/dependency_graph.py` — `format_for_llm_prompt()`

- Remove `max_edges=150` default. New signature: `max_edges: int = 500`.
- All edges shown unless exceeding 500 (safety cap for extreme repos).

### Cluster presentation in planner prompts

When building Phase 1 and Phase 2 prompts (Section 5), sub-clusters replace the old cluster hints:
- No truncation of clusters or files within clusters.
- Safety cap at 30 sub-clusters. Beyond that, show first 30 + "... and N more clusters".

---

## 4. Dynamic Page Count

### File: `worker/pipeline/wiki_planner.py`

Add function:

```python
def _suggest_page_range(file_count: int, entity_count: int) -> tuple[int, int]:
    """Suggest min/max page count based on repo complexity."""
```

Heuristic table:

| Files | Entities | Min | Max |
|-------|----------|-----|-----|
| < 10 | any | 3 | 6 |
| 10–30 | < 50 | 5 | 12 |
| 10–30 | ≥ 50 | 8 | 15 |
| 30–100 | < 150 | 10 | 25 |
| 30–100 | ≥ 150 | 15 | 35 |
| 100–300 | any | 20 | 50 |
| 300+ | any | 30 | 70 |

Inject into the Phase 1 outline prompt (Section 5):

```
- Create between {min} and {max} pages. Prefer more granular pages over broad
  ones — a focused page covering 3-5 related files is better than a sprawling
  page covering 15+. Each page should have a clear, single responsibility.
```

The range is guidance for the LLM. Enforcement happens in validation (Section 6).

---

## 5. Two-Phase Planning

### File: `worker/pipeline/wiki_planner.py`

Replace the single `generate_structured` call in `generate_wiki_plan()` with two phases.

#### Phase 1 — Outline Generation

New function:

```python
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
```

Schema:

```json
{
  "pages": [
    {
      "title": "string",
      "purpose": "string",
      "parent": "string | null"
    }
  ]
}
```

System prompt emphasizes structural thinking: "Think about the conceptual architecture. What are the major subsystems? What would a developer need to learn? Create a logical hierarchy of wiki pages that helps developers understand this project." No mention of files in the instruction.

The prompt still includes file summaries and dependency info as context — the LLM needs to know what exists. It just doesn't have to assign files.

#### Phase 2 — File Assignment

New function:

```python
async def _assign_files(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    max_retries: int = 3,
) -> dict:
    """Phase 2: Assign every file to a page from the outline."""
```

Schema:

```json
{
  "assignments": [
    {
      "file": "string",
      "page_title": "string"
    }
  ]
}
```

System prompt: "Given this wiki structure, assign every source file to the single most appropriate page. Files that are tightly coupled (import each other) should be on the same page when possible."

#### Orchestrator

`generate_wiki_plan()` becomes:

1. Compute `page_range` via `_suggest_page_range()`
2. Call `_generate_outline()` — retry up to `max_retries` on validation failure
3. Call `_assign_files()` — retry up to `max_retries` on validation failure
4. Merge outline + assignments into `WikiPlan`
5. Run `validate_wiki_plan()`

Fallbacks:
- Phase 1 fails after all retries → cluster-based fallback plan (existing logic)
- Phase 2 fails after all retries → distribute files round-robin (sorted by path) across outline pages

#### Removed

The old `_build_prompt()` function is replaced by phase-specific prompt builders. The old `_WIKI_PLAN_SCHEMA` is replaced by the two phase-specific schemas.

---

## 6. Enhanced Plan Validation

### File: `worker/pipeline/wiki_planner.py`

Update `validate_wiki_plan()` signature:

```python
def validate_wiki_plan(
    raw: dict,
    all_files: list[str] | None = None,
    existing_titles: set[str] | None = None,
    clusters: list[list[str]] | None = None,
    page_range: tuple[int, int] | None = None,
) -> WikiPlan:
```

New validation rules (all raise `ValueError` to trigger LLM retry, except the last):

| Rule | Error message template |
|------|----------------------|
| Any page has >25 files | `"Page '{title}' has {n} files — split into focused sub-pages of ≤25 files each"` |
| Non-overview page has 0 files | `"Page '{title}' has no files assigned — either assign files or remove it"` |
| Hierarchy depth > 4 | `"Wiki hierarchy is {depth} levels deep — flatten to at most 4 levels"` |
| Flat plan (depth=1) for repos with >30 files | `"All pages are top-level — create 2-3 levels of hierarchy for a repo with {n} files"` |
| Page count < `page_range[0]` | `"Plan has {n} pages but minimum is {min} — create more granular pages"` |
| Cluster files scattered across >3 pages | Warning logged only (not a retry trigger) |

Validation order: structural checks first (existing), then semantic checks (new). This ensures the LLM gets the most actionable error message on each retry.

---

## 7. Multi-Agent Bottom-Up Generation

### File: `worker/pipeline/page_generator.py`

#### `compute_generation_order()`

```python
def compute_generation_order(plan: WikiPlan) -> list[list[WikiPageSpec]]:
    """Return pages grouped by depth level, deepest first.

    Returns: [[deepest pages], ..., [root pages]]
    """
```

Algorithm: Build a title→children map. BFS from roots to compute depth. Group by depth, reverse order.

#### Updated `generate_page()`

New parameter:

```python
async def generate_page(
    spec: WikiPageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    child_contents: list[PageResult] | None = None,
    ...
) -> PageResult:
```

When `child_contents` is provided:
- Append a "Child Pages" section to the prompt with full Markdown of each child
- Switch to the parent-specific instruction template (see below)
- Skip RAG retrieval for content already covered by children (still retrieve for the parent's own files)

#### Parent instruction template

```
Write a wiki page for "{title}" that serves as the entry point for its
child pages. Structure:

## Overview
What this subsystem/area does and why it exists. High-level narrative.

## Architecture
How the child components fit together. Include a Mermaid diagram showing
the relationships and data flow between child components.

## Key Design Decisions
Important architectural choices that span multiple child components.

## How It Works
End-to-end flow tying the child components together.

Do NOT duplicate content from child pages — reference them by name.
Output Markdown only.
```

#### Batch generation helper

New function:

```python
async def generate_page_batch(
    specs: list[tuple[WikiPageSpec, list[PageResult] | None]],
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    file_analysis: FileAnalysis,
    dep_graph: DependencyGraph,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
) -> list[PageResult]:
    """Generate all pages in a batch using llm.generate_batch().

    Constructs prompts for each page (including child contents for parents),
    embeds RAG queries, then calls generate_batch with all prompts.
    """
```

This function:
1. For each spec, constructs the full prompt (RAG retrieval + entity details + dep info + child contents)
2. Collects all prompts
3. Calls `llm.generate_batch(prompts, system=system)`
4. Wraps results into `PageResult` objects

### File: `worker/jobs.py`

#### `run_full_index` — replace flat loop

Replace lines 539–582 (the flat `for i, page_spec in enumerate(plan.pages)` loop):

```python
# Stage 6: Bottom-up page generation
levels = compute_generation_order(plan)
generated: dict[str, PageResult] = {}

for depth_idx, level in enumerate(levels):
    # Collect child contents for each page in this level
    specs_with_children = []
    for page_spec in level:
        children = [
            generated[p.slug]
            for p in plan.pages
            if p.parent == page_spec.title and p.slug in generated
        ]
        specs_with_children.append((page_spec, children or None))

    results = await generate_page_batch(
        specs_with_children, store, llm, embedding,
        repo_name=name, file_analysis=file_analysis,
        dep_graph=dep_graph, on_retry=_on_retry,
        wiki_language=wiki_language,
    )

    for result, (page_spec, _) in zip(results, specs_with_children):
        generated[result.slug] = result
        # Write to DB and disk
        page_order = ...  # sequential order across all levels
        async with get_session(db_path) as s:
            s.add(WikiPage(...))
            await s.commit()
        await _write_text_async(wiki_dir / f"{result.slug}.md", result.content)

    # Update progress
    await _update_job(db_path, job_id, progress=..., status_description=...)
```

#### `run_refresh_index` — same bottom-up treatment

The refresh path's Stage 6 gets the same bottom-up ordering. Preserved (unchanged) pages are available as `child_contents` if their parent is being regenerated — load their content from disk.

---

## Testing Strategy

Each improvement gets its own test additions:

1. **generate_batch**: Test default gather impl, test max_concurrency limits, test LoggingLLMProvider wrapping
2. **Richer summaries**: Test `to_llm_summary()` with dep_graph param, verify import/docstring lines
3. **Sub-clustering**: Test `_split_large_cluster()` with clusters of various sizes, verify max_size respected
4. **Page range**: Test `_suggest_page_range()` at each boundary
5. **Two-phase planning**: Test `_generate_outline()` and `_assign_files()` independently, test orchestrator fallbacks
6. **Validation**: Test each new rule triggers ValueError with correct message
7. **Bottom-up generation**: Test `compute_generation_order()` with various tree shapes, test `generate_page_batch()`, test parent prompt includes child content
