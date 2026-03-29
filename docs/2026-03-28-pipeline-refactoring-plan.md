# Pipeline Refactoring Plan

## Context

The current 6-stage pipeline groups files into "modules" by top-level directory, which prevents identifying granular logical sub-modules. AST analysis runs redundantly (twice on every file). The wiki planner receives coarse directory-based groupings and must infer structure. This refactoring makes the LLM the source of logical structure, eliminates redundancy, and adopts a new wiki.json format that prepares for Phase 4 user steering.

## New Pipeline Stages

```
Stage 1: Ingestion         (5→20%)   — unchanged
Stage 2: AST Analysis      (20→35%)  — SINGLE pass, produces FileAnalysis
Stage 3: Dependency Graph   (35→45%)  — cleaner boundaries, file-level output
Stage 4: RAG Indexer        (45→55%)  — unchanged (uses FileAnalysis for entities)
Stage 5: Wiki Planner       (55→70%)  — LLM generates logical page tree + file assignments
Stage 6: Page Generator     (70→97%)  — uses WikiPageSpec (files, purpose)
Stage 7: Architecture Diagram (97→100%) — uses WikiPlan instead of module_tree
```

## New Data Structures

### FileAnalysis (`worker/pipeline/ast_analysis.py`)

Replaces `build_module_tree`, `build_enhanced_module_tree`, and `_build_file_entities`.

```python
@dataclass
class FileInfo:
    rel_path: str
    entities: list[dict]    # full entity list from analyze_file()
    class_count: int
    function_count: int
    summary: str            # comma-joined top entity names

@dataclass
class FileAnalysis:
    files: dict[str, FileInfo]   # keyed by rel_path

    def to_llm_summary(self, max_files: int = 200) -> str:
        """Compact per-file summaries for the LLM planner prompt."""
```

### WikiPlan / WikiPageSpec (`worker/pipeline/wiki_planner.py`)

Replaces `PagePlan` / `PageSpec`.

```python
@dataclass
class WikiPageSpec:
    title: str
    purpose: str                         # was "description"
    parent: str | None = None            # parent page TITLE (not slug)
    page_notes: list[dict] | None = None # [{"content": ""}] — Phase 4 prep
    files: list[str] | None = None       # rel_paths assigned by LLM

    @property
    def slug(self) -> str:               # derived, not stored in wiki.json
        return re.sub(r"[^a-z0-9-]+", "-", self.title.lower()).strip("-")

    @property
    def parent_slug(self) -> str | None:
        if self.parent is None:
            return None
        return re.sub(r"[^a-z0-9-]+", "-", self.parent.lower()).strip("-")

@dataclass
class WikiPlan:
    repo_notes: list[dict]      # [{"content": ""}] — Phase 4 prep
    pages: list[WikiPageSpec]

    def to_wiki_json(self) -> dict:
        """User-facing wiki.json (no slugs, no files)."""

    def to_internal_json(self) -> dict:
        """Pipeline-internal format (includes files for refresh)."""

    def to_api_structure(self) -> dict:
        """API-compatible format (includes derived slugs/parent_slugs)."""
```

**wiki.json format** (written to `wiki/wiki.json`):
```json
{
  "repo_notes": [{"content": ""}],
  "pages": [
    {"title": "Overview", "purpose": "High-level introduction...", "page_notes": [{"content": ""}]},
    {"title": "Engine Architecture", "purpose": "Core engine components...", "page_notes": [{"content": ""}]},
    {"title": "Scheduler", "purpose": "Scheduling algorithms...", "parent": "Engine Architecture", "page_notes": [{"content": ""}]}
  ]
}
```

**Internal format** (written to `ast/wiki_plan.json` — includes file mappings for refresh):
```json
{
  "repo_notes": [{"content": ""}],
  "pages": [
    {"title": "Overview", "purpose": "...", "files": ["README.md", "main.py"]},
    {"title": "Engine Architecture", "purpose": "...", "files": ["engine/core.py", "engine/client.py"]},
    {"title": "Scheduler", "purpose": "...", "parent": "Engine Architecture", "files": ["engine/scheduler.py"]}
  ]
}
```

**API structure** (stored in `Repository.wiki_structure` — includes derived slugs for frontend):
```json
{
  "pages": [
    {"title": "Overview", "slug": "overview", "parent_slug": null, "description": "High-level introduction..."},
    {"title": "Engine Architecture", "slug": "engine-architecture", "parent_slug": null, "description": "Core engine..."},
    {"title": "Scheduler", "slug": "scheduler", "parent_slug": "engine-architecture", "description": "Scheduling..."}
  ]
}
```

This keeps `description` and `parent_slug` keys in the API response — **zero frontend/API changes needed**.

## LLM Wiki Planner Changes

The planner prompt changes from receiving a directory-grouped module tree to receiving:

1. **File-level summaries** from `FileAnalysis.to_llm_summary()` — flat list of files with entity counts + top entity names
2. **README excerpt**
3. **File-level dependency edges** + clusters from `DependencyGraph`

The LLM outputs the new schema:
```json
{
  "pages": [{
    "title": "string",
    "purpose": "string",
    "parent": "string | null",
    "files": ["string"]
  }]
}
```

**Validation** (`validate_wiki_plan`):
- Every repo file must appear in at least one page (orphans appended to Overview)
- Parent titles must reference existing page titles
- At least one page must exist
- Fallback: flat plan with Overview + one page per dependency cluster

## File-by-File Changes

### `worker/pipeline/ast_analysis.py`
- **Remove**: `build_module_tree()`, `build_enhanced_module_tree()`
- **Add**: `FileInfo`, `FileAnalysis` dataclasses, `analyze_all_files(root, files) -> FileAnalysis`
- **Keep**: `analyze_file()`, all tree-sitter logic unchanged

### `worker/pipeline/wiki_planner.py`
- **Remove**: `PageSpec`, `PagePlan`, `_PLAN_SCHEMA`, `validate_page_plan()`
- **Add**: `WikiPageSpec`, `WikiPlan`, new `_WIKI_PLAN_SCHEMA`, `validate_wiki_plan()`
- **Modify**: `generate_page_plan()` → `generate_wiki_plan()` — new signature takes `FileAnalysis` + `DependencyGraph`
- **Modify**: `_build_prompt()` — takes file summaries + dep graph instead of enhanced_tree

### `worker/pipeline/page_generator.py`
- **Modify**: Accept `WikiPageSpec` instead of `PageSpec`
- **Modify**: `_build_page_prompt()` — use `spec.purpose` and `spec.files` instead of `spec.description` and `spec.modules`
- **Keep**: RAG multi-query logic, `PageResult`, formatting helpers

### `worker/pipeline/dependency_graph.py`
- **Remove**: `summarize_dependencies()`
- **Add**: `format_for_llm_prompt(graph, max_edges=150) -> str` — formats file-level deps for planner
- **Add**: `summarize_page_deps(page_files, graph) -> dict` — per-page dep summary for page generator
- **Keep**: `DependencyGraph`, `build_dependency_graph()`, `_compute_clusters()`

### `worker/pipeline/diagram_synthesis.py`
- **Modify**: `synthesize_diagrams()` accepts `WikiPlan` instead of `module_tree`
- Format page titles as diagram nodes instead of directory names

### `worker/pipeline/ingestion.py`
- **Remove**: `get_affected_modules()`
- **Add**: `get_affected_pages(changed_files, wiki_plan) -> set[str]` — returns titles of affected pages
- **Keep**: everything else unchanged

### `worker/jobs.py`
- **Remove**: `_build_file_entities()`, `_build_module_entity_map()`, `_build_module_files()`, `_collect_page_context()`
- **Remove**: imports of `build_module_tree`, `build_enhanced_module_tree`, `summarize_dependencies`
- **Add**: `_collect_page_entities(page_spec, file_analysis) -> list[dict]`
- **Add**: `_collect_page_deps(page_spec, dep_graph) -> dict`
- **Modify**: `run_full_index()` — new stage flow using `FileAnalysis` and `WikiPlan`
- **Modify**: `run_refresh_index()` — load `wiki_plan.json` instead of `module_tree.json`, use `get_affected_pages()`
- **Persist**: `wiki/wiki.json` (user-facing), `ast/wiki_plan.json` (internal with files), `Repository.wiki_structure` (API-compatible with slugs)

### `shared/models.py` — No changes
### `api/` — No changes
### `web/` — No changes

## Impact on `run_refresh_index`

1. Load previous `WikiPlan` from `ast/wiki_plan.json` (replaces `module_tree.json`)
2. `get_affected_pages(changed_files, wiki_plan)` → set of page titles with overlapping files
3. If files added/removed (not just modified), fall back to full re-planning
4. Otherwise, re-plan only affected pages (pass existing plan context to LLM)
5. Re-generate only affected pages

## Test Updates

### `tests/worker/test_ast_analysis.py`
- Remove tests for `build_module_tree`, `build_enhanced_module_tree`
- Add tests for `analyze_all_files`, `FileAnalysis.to_llm_summary()`

### `tests/worker/test_wiki_planner.py`
- Rewrite for `WikiPageSpec`/`WikiPlan`, `validate_wiki_plan()`, `generate_wiki_plan()`

### `tests/worker/test_page_generator.py`
- Update `PageSpec` → `WikiPageSpec`, `modules` → `files`, `description` → `purpose`

### `tests/worker/test_dependency_graph.py`
- Remove `test_summarize_dependencies*`
- Add tests for `format_for_llm_prompt()`, `summarize_page_deps()`

### `tests/worker/test_diagram_synthesis.py`
- Update to pass `WikiPlan` instead of `module_tree`

### `tests/worker/test_jobs.py`, `tests/worker/test_refresh.py`, `tests/api/test_repos.py`
- Update `module_tree.json` references → `wiki_plan.json`
- Update mock LLM return values to new schema

### `tests/worker/test_ingestion.py`
- Replace `test_get_affected_modules` with `test_get_affected_pages`

## Implementation Order

1. `ast_analysis.py` — new `FileInfo`, `FileAnalysis`, `analyze_all_files()`; remove old builders
2. `wiki_planner.py` — new `WikiPageSpec`, `WikiPlan`, schema, prompt, validation
3. `dependency_graph.py` — add `format_for_llm_prompt()`, `summarize_page_deps()`; remove `summarize_dependencies()`
4. `page_generator.py` — accept `WikiPageSpec`, use `purpose`/`files`
5. `diagram_synthesis.py` — accept `WikiPlan`
6. `ingestion.py` — replace `get_affected_modules` with `get_affected_pages`
7. `jobs.py` — wire everything together, remove old helpers
8. Update all tests
9. End-to-end verification: `pytest tests/ --ignore=tests/e2e` + `ruff check . && ruff format --check .`

## Verification

```bash
# Unit tests
pytest tests/ --ignore=tests/e2e

# Lint
uv run ruff check .
uv run ruff format --check .
cd web && npm run lint

# Manual E2E (optional)
# autowiki index github.com/some/small-repo
# Inspect wiki.json, wiki_plan.json, generated pages
```
