# Page-Centric File Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current "partition every file into exactly one page" planner Phase 2 with a page-centric selection model that picks 5–8 (range 3–10) representative source code files per wiki page, eliminating the mandatory-coverage constraint.

**Architecture:** Phase 2 of the wiki planner changes its LLM schema from file-centric assignment (`{file → primary_page}`) to page-centric selection (`{page_title → [files]}`). A pre-filtering step uses the existing token-matching logic to narrow each page's candidate pool to ~25 files before the LLM selects the best 5–8. A scoring function (entity density + dependency in-degree + file type + semantic alignment) powers the heuristic fallback. `WikiPlan` gains an `all_repo_files` snapshot so incremental refresh can detect added/removed files without deriving them from the now-smaller `page.files` list.

**Tech Stack:** Python, asyncio, pydantic-free dataclasses, FAISS (unchanged), ARQ worker, existing `async_retry` / `PromptSegment` / `pipeline_logging` patterns.

---

## Design Decisions

### File count target: 5–8, range 3–10

- Hard max `MAX_FILES_PER_PAGE = 10` (down from 50). `page_outline.py:242` computes `n_sections = max(3, len(files) // 2)` — with 5–10 files this yields 3–5 outline sections, the right granularity.
- Hard min enforced in validation: 1 file (structural parent/overview pages may legitimately have 0 and are exempted as before).
- Prompt instructs LLM to target **5–8**, accepting 3–10.
- `page_generator.py:202` takes only `spec.files[:5]` for the RAG query string. Selected files are **sorted by relevance score** (descending) after LLM selection, so `[:5]` always yields the highest-scored subset. The 6th–10th files only affect entity extraction and the source-files table.

### RAG retrieval is not affected by smaller `page.files`

The FAISS index is built from all repository files and searched without restriction (`build_rag_index` in `jobs.py`). `spec.files` only contributes path tokens to the query string (line 202: `f"{spec.title} {' '.join(spec.files[:5])}"`) and drives entity extraction/dependency summary. Semantic similarity will surface relevant chunks from non-selected files automatically. `spec.purpose` (line 203–204) provides topical coverage independent of which files are selected. Generation prose quality is unchanged; entity lists and the source-files table become more focused — an improvement.

### `secondary_files` retained but not produced by new Phase 2

The existing `secondary_files` field and its page-generator injection ("Referenced modules" block) remain untouched. The new selection step returns only primary selections; `secondary_files` defaults to `[]`. This avoids breaking `page_generator.py:370–374` and is a safe no-op — future work can reintroduce cross-page referencing as an explicit selection option.

### Orphan enforcement removed

`validate_wiki_plan`'s "critical orphan" check (`VALIDATION_FAILURE: N core source files are missing`) is deleted. Files not selected by any page are intentionally omitted. Low-priority file logging (line 1337–1340) is also removed since the concept no longer applies.

### `WikiPlan.all_repo_files` for refresh

`jobs.py:932` currently computes `old_all_files = {f for p in old_plan.pages for f in p.files}`. With `page.files` now containing only 3–10 files per page, this union is much smaller than the actual repo, producing a massively inflated `added_files` set that would mark the Overview page stale on every refresh. Fix: add `all_repo_files: list[str]` to `WikiPlan`, persisted in `ast/wiki_plan.json`, and read it in the refresh comparison.

---

## Files Changed

| File | Change |
|---|---|
| `worker/pipeline/wiki_planner.py` | Primary: constants, schema, scoring, pre-filter, prompt builders, batch executor, fallback, validation, `WikiPlan.all_repo_files` |
| `worker/jobs.py` | Load `all_repo_files` from plan JSON; use it in refresh comparison (lines 833–848, 932–935) |
| `tests/test_wiki_planner.py` | New tests for scoring, pre-filter, selection validation, fallback, serialization |

No changes to `page_generator.py`, `page_draft.py`, `page_outline.py`, `ingestion.py`, or `fixture_recorder.py`.

---

## Task 1: Constants, schema, and file-type sets

**Files:**
- Modify: `worker/pipeline/wiki_planner.py:49–51` (constants block)

- [ ] **Step 1: Replace `MAX_FILES_PER_PAGE` and add `MIN_FILES_PER_PAGE` and file-type sets**

Replace lines 49–51:
```python
#: Maximum number of files allowed on a single wiki page to ensure focus.
MAX_FILES_PER_PAGE = 50
```

With:
```python
#: Hard maximum and soft minimum for representative files per wiki page.
MAX_FILES_PER_PAGE = 10
MIN_FILES_PER_PAGE = 3

_CODE_EXTS: frozenset[str] = frozenset(
    {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
        ".java", ".cpp", ".c", ".cs", ".rb", ".swift", ".kt", ".scala",
    }
)
_DOC_EXTS: frozenset[str] = frozenset({".md", ".rst", ".txt", ".adoc"})
_CONFIG_EXTS: frozenset[str] = frozenset(
    {".json", ".yaml", ".yml", ".toml", ".ini", ".env", ".cfg"}
)
```

- [ ] **Step 2: Replace `_ASSIGNMENT_SCHEMA` with `_SELECTION_SCHEMA`**

Replace the `_ASSIGNMENT_SCHEMA` block (lines 310–331) with:
```python
_SELECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "selections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_title": {"type": "string"},
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["page_title", "files"],
            },
        }
    },
    "required": ["selections"],
}
```

- [ ] **Step 3: Update `_SYSTEM` prompt — remove "every source file must be assigned"**

In `_SYSTEM` (line 333–359), replace:
```python
    "Each page should have a clear PURPOSE — it should "
    "explain a concept, component, or workflow. Every source "
    "file must be assigned to exactly one page.\n\n"
    "Output ONLY valid JSON."
```
With:
```python
    "Each page should have a clear PURPOSE — it should "
    "explain a concept, component, or workflow. Pages are "
    "represented by 5–8 of their most representative source "
    "code files — full coverage of every file is not required.\n\n"
    "Output ONLY valid JSON."
```

- [ ] **Step 4: Update the outline prompt guideline text**

In `_build_outline_prompt` (line 428–448), replace:
```python
        "- Create between {min_pages} and {max_pages} pages. Prefer more granular "
        "pages over broad ones — a focused page covering 3-5 related files is better "
        "than a sprawling page covering 15+. Each page should have a clear, single "
        "responsibility.\n"
```
With:
```python
        f"- Create between {min_pages} and {max_pages} pages. Prefer more granular "
        "pages over broad ones — a focused page covering 5–8 representative code files "
        "is better than a sprawling page covering 20+. Each page should have a clear, "
        "single responsibility.\n"
```

And replace the sentence referencing file assignment in the outline prompt footer:
```python
        "- Do NOT assign files to pages — just define the page structure\n\n"
```
(This line is already correct — no file assignment in Phase 1. No change needed here.)

- [ ] **Step 5: Commit**

```bash
cd /Users/lazyxiang/code/AutoWiki
git add worker/pipeline/wiki_planner.py
git commit -m "refactor(planner): replace assignment schema with page-centric selection schema; reduce MAX_FILES_PER_PAGE 50→10"
```

---

## Task 2: File scoring function and candidate pre-filter

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — add after `_best_matching_page` (around line 864)
- Test: `tests/test_wiki_planner.py`

- [ ] **Step 1: Write failing tests for `_score_file_for_page` and `_prefilter_candidates`**

Add to `tests/test_wiki_planner.py`:

```python
from worker.pipeline.wiki_planner import (
    _score_file_for_page,
    _prefilter_candidates,
)


class FakeFileInfo:
    def __init__(self, entities):
        self.entities = entities


def _fake_infos(*paths_entities):
    return {path: FakeFileInfo(ents) for path, ents in paths_entities}


def test_score_prefers_code_over_doc():
    page = {"title": "API Gateway", "purpose": "Handles HTTP routing."}
    infos = _fake_infos(("api/routes.py", ["route_a", "route_b"]), ("docs/api.md", []))
    code_score = _score_file_for_page("api/routes.py", page, infos, None)
    doc_score = _score_file_for_page("docs/api.md", page, infos, None)
    assert code_score > doc_score


def test_score_entity_density():
    page = {"title": "Worker", "purpose": "Background jobs."}
    sparse = _fake_infos(("worker/job.py", ["run"]))
    dense = _fake_infos(("worker/job.py", [f"fn{i}" for i in range(15)]))
    assert (
        _score_file_for_page("worker/job.py", page, dense, None)
        > _score_file_for_page("worker/job.py", page, sparse, None)
    )


def test_score_semantic_alignment():
    page = {"title": "API Gateway", "purpose": "Routes requests."}
    infos = _fake_infos(("api/gateway.py", ["route"]), ("util/helper.py", ["route"]))
    # api/gateway.py matches "api" and "gateway" tokens — should score higher
    assert (
        _score_file_for_page("api/gateway.py", page, infos, None)
        > _score_file_for_page("util/helper.py", page, infos, None)
    )


def test_prefilter_returns_at_most_max_candidates():
    page = {"title": "Worker", "purpose": "Background jobs."}
    all_files = [f"worker/file{i}.py" for i in range(50)]
    infos = {f: FakeFileInfo([f"fn{i}"]) for i, f in enumerate(all_files)}
    result = _prefilter_candidates(page, all_files, infos, None, max_candidates=10)
    assert len(result) <= 10


def test_prefilter_prefers_code_files():
    page = {"title": "Auth", "purpose": "Authentication logic."}
    all_files = ["auth/login.py", "auth/README.md", "auth/config.yaml"]
    infos = {
        "auth/login.py": FakeFileInfo(["authenticate"]),
        "auth/README.md": FakeFileInfo([]),
        "auth/config.yaml": FakeFileInfo([]),
    }
    result = _prefilter_candidates(page, all_files, infos, None)
    assert result[0] == "auth/login.py"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/lazyxiang/code/AutoWiki
uv run pytest tests/test_wiki_planner.py::test_score_prefers_code_over_doc tests/test_wiki_planner.py::test_prefilter_returns_at_most_max_candidates -v 2>&1 | tail -20
```
Expected: `ImportError` or `AttributeError` — `_score_file_for_page` not yet defined.

- [ ] **Step 3: Add `_score_file_for_page` to `wiki_planner.py`**

Add after `_best_matching_page` (after line ~864):

```python
def _score_file_for_page(
    path: str,
    page: dict,
    file_infos: "dict[str, Any]",
    dep_graph: "DependencyGraph | None",
) -> float:
    """Score how well a file represents a wiki page.

    Higher is better.  Used for heuristic fallback file selection.
    """
    score = 0.0
    lower = path.lower()
    ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""

    if ext in _CODE_EXTS:
        score += 3.0
    elif ext in _DOC_EXTS:
        basename = lower.rsplit("/", 1)[-1] if "/" in lower else lower
        # Top-level README is welcome on the overview page only
        if "/" not in path and basename.startswith("readme"):
            score += 2.0 if "overview" in page["title"].lower() else -2.0
        else:
            score -= 2.0
    elif ext in _CONFIG_EXTS:
        score -= 1.5

    # Entity density bonus (cap at 3.0)
    info = file_infos.get(path)
    if info is not None:
        score += min(len(info.entities) * 0.4, 3.0)

    # Dependency in-degree bonus — how many files import this one (cap at 2.0)
    if dep_graph is not None:
        in_degree = sum(1 for deps in dep_graph.edges.values() if path in deps)
        score += min(in_degree * 0.3, 2.0)

    # Semantic alignment with page title + purpose
    page_tokens = _tokenize(page["title"] + " " + page.get("purpose", ""))
    file_tokens = _tokenize(
        path.replace("/", " ").replace("_", " ").replace("-", " ")
    )
    score += len(page_tokens & file_tokens) * 0.5

    return score
```

Note: add `from typing import Any` if not already imported; `Any` is used for the `file_infos` type hint to avoid a circular import. Alternatively use `object` or quote the full type.

- [ ] **Step 4: Add `_prefilter_candidates` to `wiki_planner.py`**

Add immediately after `_score_file_for_page`:

```python
def _prefilter_candidates(
    page: dict,
    all_files: list[str],
    file_infos: "dict[str, Any]",
    dep_graph: "DependencyGraph | None",
    max_candidates: int = 25,
) -> list[str]:
    """Return the top-scored candidate files for a page (at most max_candidates).

    Files with a score ≤ 0 are excluded — they are not relevant to this page.
    """
    scored = [
        (f, _score_file_for_page(f, page, file_infos, dep_graph))
        for f in all_files
    ]
    scored = [(f, s) for f, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [f for f, _ in scored[:max_candidates]]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/lazyxiang/code/AutoWiki
uv run pytest tests/test_wiki_planner.py::test_score_prefers_code_over_doc tests/test_wiki_planner.py::test_score_entity_density tests/test_wiki_planner.py::test_score_semantic_alignment tests/test_wiki_planner.py::test_prefilter_returns_at_most_max_candidates tests/test_wiki_planner.py::test_prefilter_prefers_code_files -v 2>&1 | tail -20
```
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/test_wiki_planner.py
git commit -m "feat(planner): add file scoring function and per-page candidate pre-filter"
```

---

## Task 3: Page-centric selection prompt builders

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — replace `_build_assignment_prompt`, `_build_batch_assignment_system`, `_build_batch_assignment_user`

- [ ] **Step 1: Remove dead code `_build_assignment_prompt`**

Delete the `_build_assignment_prompt` function (lines 452–480). It is never called from `_assign_files` (which uses the batch path exclusively) and will not be needed in the new model.

- [ ] **Step 2: Replace `_build_batch_assignment_system` with `_build_selection_system`**

Replace `_build_batch_assignment_system` (lines 483–510) with:

```python
def _build_selection_system(
    file_summary: str,
    dep_info: str | None,
) -> list[PromptSegment]:
    """Cacheable system segment for page-centric file selection.

    Contains full repository context reused across all page batches so only
    the first batch pays full token cost on Anthropic's prompt cache.
    """
    parts: list[str] = [
        "You are selecting representative source files for wiki pages.",
        "",
        "## Repository file summaries:",
        file_summary,
    ]
    if dep_info:
        parts += ["", "## Dependency relationships:", dep_info]
    return [PromptSegment(text="\n".join(parts), cacheable=True)]
```

- [ ] **Step 3: Replace `_build_batch_assignment_user` with `_build_selection_user`**

Replace `_build_batch_assignment_user` (lines 513–546) with:

```python
def _build_selection_user(
    pages_with_candidates: "list[tuple[str, str, list[str]]]",
    last_error: str | None = None,
) -> PromptSegment:
    """Per-batch user segment listing pages and their candidate files.

    Args:
        pages_with_candidates: List of (title, purpose, candidate_file_paths).
        last_error: Validation error from previous attempt, fed back as context.
    """
    schema_json = json.dumps(_SELECTION_SCHEMA, indent=2)
    pages_str = "\n\n".join(
        f'Page: "{title}"\nPurpose: {purpose}\nCandidates:\n'
        + "\n".join(f"  - {f}" for f in candidates)
        for title, purpose, candidates in pages_with_candidates
    )
    text = (
        f"For each wiki page below, select the {MIN_FILES_PER_PAGE}–{MAX_FILES_PER_PAGE} "
        "source code files from its candidate list that best represent the page's content.\n\n"
        "Rules:\n"
        "- Strongly prefer code files (.py, .ts, .go, .rs, .java, etc.) over "
        ".md / .yaml / .json files\n"
        "- Include only files that contain substantial relevant code "
        "(functions, classes, core logic)\n"
        "- Configuration files only when central to understanding this page's architecture\n"
        "- README.md only on a top-level Overview page\n"
        f"- Target 5–8 files per page; fewer is fine when fewer candidates are relevant\n"
        "- You may select fewer than 3 only when genuinely fewer relevant files exist\n\n"
        f"Pages:\n{pages_str}\n\n"
    )
    if last_error:
        text += f"CRITICAL: Previous attempt failed with error: {last_error}\n\n"
    text += f"Output JSON matching this schema:\n{schema_json}"
    return PromptSegment(text=text, cacheable=False)
```

- [ ] **Step 4: Verify linting**

```bash
cd /Users/lazyxiang/code/AutoWiki
uv run ruff check worker/pipeline/wiki_planner.py
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py
git commit -m "refactor(planner): replace batch-assignment prompt builders with page-centric selection builders"
```

---

## Task 4: `_select_files_in_batches` and `_select_files`

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — replace `_assign_files_in_batches` and `_assign_files`

- [ ] **Step 1: Replace `_assign_files_in_batches` with `_select_files_in_batches`**

Replace `_BATCH_SIZE_DEFAULT = 40` and `_assign_files_in_batches` (lines 1023–1146) with:

```python
_PAGE_BATCH_SIZE = 12  # pages per LLM selection call


async def _select_files_in_batches(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    file_infos: "dict[str, Any]",
    dep_graph: "DependencyGraph | None",
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    last_error: str | None = None,
) -> dict[str, list[str]]:
    """Phase 2: Ask the LLM to select representative files for each page.

    Pages are batched (_PAGE_BATCH_SIZE at a time).  Each page receives a
    pre-filtered candidate list (~25 files) so the LLM works with focused
    context.  The first batch runs serially to warm the Anthropic prompt cache;
    remaining batches run in parallel.
    """
    import asyncio

    all_files_set = set(all_files)
    valid_titles = [p["title"] for p in outline]
    result: dict[str, list[str]] = {t: [] for t in valid_titles}

    stage_system_seg = PromptSegment(text=system, cacheable=False)
    context_segs = _build_selection_system(file_summary, dep_info)
    system_segs: list[PromptSegment] = [stage_system_seg, *context_segs]

    async def _run_page_batch(batch_pages: list[dict]) -> None:
        pages_with_candidates = [
            (
                p["title"],
                p.get("purpose", ""),
                _prefilter_candidates(p, all_files, file_infos, dep_graph),
            )
            for p in batch_pages
        ]
        user_seg = _build_selection_user(pages_with_candidates, last_error)
        try:
            raw = await async_retry(
                llm.generate_structured,
                [user_seg],
                schema=_SELECTION_SCHEMA,
                system=system_segs,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
        except Exception as exc:
            log_validation_retry(
                logger,
                stage="wiki_planner.select_files.batch",
                attempt=1,
                max_retries=1,
                exc=exc,
                context={"batch_pages": len(batch_pages)},
            )
            return
        title_to_page = {p["title"]: p for p in batch_pages}
        for sel in raw.get("selections", []):
            title = sel.get("page_title", "")
            files = sel.get("files", [])
            if title not in result:
                continue
            # Keep only files that actually exist in the repo, clamp to max,
            # then sort by relevance score so spec.files[:5] used by the RAG
            # query in page_generator.py:202 is always the most relevant subset.
            valid = [f for f in files if f in all_files_set][:MAX_FILES_PER_PAGE]
            page_dict = title_to_page.get(title, {})
            valid.sort(
                key=lambda f: _score_file_for_page(f, page_dict, file_infos, dep_graph),
                reverse=True,
            )
            result[title] = valid

    batches: list[list[dict]] = [
        outline[i : i + _PAGE_BATCH_SIZE]
        for i in range(0, len(outline), _PAGE_BATCH_SIZE)
    ]
    if batches:
        await _run_page_batch(batches[0])  # serial first batch warms cache
    if len(batches) > 1:
        await asyncio.gather(*(_run_page_batch(b) for b in batches[1:]))

    return result
```

- [ ] **Step 2: Replace `_assign_files` with `_select_files`**

Replace `_assign_files` (lines 1149–1216) with:

```python
async def _select_files(
    outline: list[dict],
    file_summary: str,
    dep_info: str | None,
    all_files: list[str],
    file_infos: "dict[str, Any]",
    dep_graph: "DependencyGraph | None",
    llm: LLMProvider,
    system: str,
    on_retry: OnRetryCallback | None,
    max_retries: int = 3,
    fast_llm: LLMProvider | None = None,
) -> dict[str, list[str]]:
    """Phase 2: Select representative files for each page with retry + feedback."""
    preferred_llm = fast_llm or llm
    last_error: str | None = None
    last_result: dict[str, list[str]] | None = None

    for attempt in range(1, max_retries + 1):
        current_llm = preferred_llm if attempt == 1 else llm
        try:
            result = await _select_files_in_batches(
                outline=outline,
                file_summary=file_summary,
                dep_info=dep_info,
                all_files=all_files,
                file_infos=file_infos,
                dep_graph=dep_graph,
                llm=current_llm,
                system=system,
                on_retry=on_retry,
                last_error=last_error,
            )
            last_result = result
            _validate_selections(result, outline)
            return result
        except ValueError as exc:
            last_error = str(exc)
            if attempt < max_retries:
                log_validation_retry(
                    logger,
                    stage="wiki_planner.select_files",
                    attempt=attempt,
                    max_retries=max_retries,
                    exc=exc,
                    context={"outline_pages": len(outline)},
                )
            else:
                log_final_failure(
                    logger,
                    stage="wiki_planner.select_files",
                    exc=exc,
                    context={"outline_pages": len(outline)},
                )

    raise ValueError(
        f"Failed to select files after {max_retries} attempts",
        last_error,
        last_result,
    )
```

- [ ] **Step 3: Verify linting**

```bash
uv run ruff check worker/pipeline/wiki_planner.py
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add worker/pipeline/wiki_planner.py
git commit -m "feat(planner): implement page-centric _select_files_in_batches and _select_files"
```

---

## Task 5: Heuristic fallback using scoring

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — replace `_heuristic_recovery_assignment`
- Test: `tests/test_wiki_planner.py`

- [ ] **Step 1: Write failing test for the new fallback**

Add to `tests/test_wiki_planner.py`:

```python
from worker.pipeline.wiki_planner import _heuristic_select_files


def test_heuristic_select_files_picks_code_over_docs():
    outline = [{"title": "Auth", "purpose": "Authentication logic."}]
    all_files = ["auth/login.py", "auth/README.md", "auth/config.yaml"]
    infos = {
        "auth/login.py": FakeFileInfo(["authenticate", "logout"]),
        "auth/README.md": FakeFileInfo([]),
        "auth/config.yaml": FakeFileInfo([]),
    }
    result = _heuristic_select_files(outline, all_files, infos, None)
    assert "auth/login.py" in result["Auth"]
    assert "auth/README.md" not in result["Auth"]


def test_heuristic_select_files_uses_partial_llm_selections():
    outline = [
        {"title": "API", "purpose": "REST endpoints."},
        {"title": "DB", "purpose": "Database models."},
    ]
    all_files = ["api/routes.py", "db/models.py"]
    infos = {
        "api/routes.py": FakeFileInfo(["get", "post"]),
        "db/models.py": FakeFileInfo(["User", "Session"]),
    }
    partial = {"API": ["api/routes.py"]}  # DB has no partial selection
    result = _heuristic_select_files(outline, all_files, infos, None, partial)
    assert result["API"] == ["api/routes.py"]  # kept from partial
    assert "db/models.py" in result["DB"]     # scored for DB


def test_heuristic_select_files_respects_max():
    outline = [{"title": "Core", "purpose": "Core logic."}]
    all_files = [f"core/module{i}.py" for i in range(30)]
    infos = {f: FakeFileInfo([f"fn{i}"]) for i, f in enumerate(all_files)}
    result = _heuristic_select_files(outline, all_files, infos, None)
    assert len(result["Core"]) <= 10  # MAX_FILES_PER_PAGE
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_wiki_planner.py::test_heuristic_select_files_picks_code_over_docs -v 2>&1 | tail -10
```
Expected: `ImportError` — `_heuristic_select_files` not yet defined.

- [ ] **Step 3: Replace `_heuristic_recovery_assignment` with `_heuristic_select_files`**

Delete `_heuristic_recovery_assignment` (lines 947–1020) and replace with:

```python
def _heuristic_select_files(
    outline: list[dict],
    all_files: list[str],
    file_infos: "dict[str, Any]",
    dep_graph: "DependencyGraph | None",
    partial_selections: "dict[str, list[str]] | None" = None,
) -> dict[str, list[str]]:
    """Score-based file selection used as Phase 2 fallback.

    Preserves valid partial selections from a failed LLM attempt, then fills
    remaining pages by pre-filtering to ~25 candidates and selecting the
    top-scoring files up to MAX_FILES_PER_PAGE.
    """
    result: dict[str, list[str]] = {}

    # Preserve valid partial selections from LLM
    if partial_selections:
        for page in outline:
            title = page["title"]
            files = partial_selections.get(title, [])
            if 0 < len(files) <= MAX_FILES_PER_PAGE:
                result[title] = list(files)

    # Score-based selection for pages not yet covered
    for page in outline:
        title = page["title"]
        if title in result:
            continue
        candidates = _prefilter_candidates(
            page, all_files, file_infos, dep_graph, max_candidates=25
        )
        if not candidates:
            result[title] = []
            continue
        scored = sorted(
            candidates,
            key=lambda f: _score_file_for_page(f, page, file_infos, dep_graph),
            reverse=True,
        )
        target = min(MAX_FILES_PER_PAGE, max(MIN_FILES_PER_PAGE, len(scored)))
        result[title] = scored[:target]

    # Ensure every page title has an entry
    for p in outline:
        result.setdefault(p["title"], [])

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_wiki_planner.py::test_heuristic_select_files_picks_code_over_docs tests/test_wiki_planner.py::test_heuristic_select_files_uses_partial_llm_selections tests/test_wiki_planner.py::test_heuristic_select_files_respects_max -v 2>&1 | tail -15
```
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/test_wiki_planner.py
git commit -m "feat(planner): replace directory-cluster fallback with score-based _heuristic_select_files"
```

---

## Task 6: Validation updates

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — `_validate_assignments` → `_validate_selections`, `validate_wiki_plan` orphan removal
- Test: `tests/test_wiki_planner.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_wiki_planner.py`:

```python
from worker.pipeline.wiki_planner import _validate_selections, validate_wiki_plan


def test_validate_selections_passes_normal():
    outline = [{"title": "API", "purpose": "REST API."}]
    result = {"API": ["api/routes.py", "api/models.py"]}
    _validate_selections(result, outline)  # should not raise


def test_validate_selections_fails_over_max():
    outline = [{"title": "API", "purpose": "REST API."}]
    result = {"API": [f"api/file{i}.py" for i in range(11)]}
    with pytest.raises(ValueError, match="VALIDATION_FAILURE"):
        _validate_selections(result, outline)


def test_validate_selections_fails_empty_leaf_page():
    outline = [{"title": "Auth", "purpose": "Login logic."}]
    result = {"Auth": []}
    with pytest.raises(ValueError, match="VALIDATION_FAILURE"):
        _validate_selections(result, outline)


def test_validate_selections_allows_empty_parent():
    outline = [
        {"title": "Backend", "purpose": "Parent."},
        {"title": "Auth", "purpose": "Login.", "parent": "Backend"},
    ]
    result = {"Backend": [], "Auth": ["auth/login.py"]}
    _validate_selections(result, outline)  # parent with no files is fine


def test_validate_wiki_plan_no_orphan_check():
    # Critical files not in any page should NOT raise
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top level.", "files": ["main.py"]},
        ]
    }
    plan = validate_wiki_plan(raw, all_files=["main.py", "worker/core.py"])
    assert plan.pages[0].title == "Overview"
    # worker/core.py is unassigned — no error raised
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_wiki_planner.py::test_validate_selections_passes_normal tests/test_wiki_planner.py::test_validate_wiki_plan_no_orphan_check -v 2>&1 | tail -15
```
Expected: `ImportError` for `_validate_selections`; `validate_wiki_plan` test may fail on the orphan check.

- [ ] **Step 3: Replace `_validate_assignments` with `_validate_selections`**

Replace `_validate_assignments` (lines 614–651) with:

```python
def _validate_selections(
    result: dict[str, list[str]],
    outline: list[dict],
) -> None:
    """Validate per-page file selection counts.

    Checks:
    - No page has more than MAX_FILES_PER_PAGE files.
    - No non-overview, non-parent leaf page has zero files.

    Raises:
        ValueError: Describing the first constraint violated.
    """
    all_parents = {p.get("parent") for p in outline if p.get("parent")}
    for page in outline:
        title = page["title"]
        files = result.get(title, [])
        if len(files) > MAX_FILES_PER_PAGE:
            raise ValueError(
                f"VALIDATION_FAILURE: Page '{title}' has {len(files)} files "
                f"(max {MAX_FILES_PER_PAGE}). Reduce to the most representative files."
            )
        is_overview = "overview" in title.lower()
        is_parent = title in all_parents
        if not (is_overview or is_parent or files):
            raise ValueError(
                f"VALIDATION_FAILURE: Page '{title}' has no files selected. "
                "Select at least one representative source file or remove this page."
            )
```

- [ ] **Step 4: Remove orphan enforcement from `validate_wiki_plan`**

In `validate_wiki_plan`, delete the orphan check block (currently around lines 1323–1340):

```python
    # Check for unassigned files (Orphans) — only critical files trigger failure
    if all_files:
        assigned = {f for page in pages for f in page.files}
        assigned |= {f for page in pages for f in page.secondary_files}
        orphans = [f for f in all_files if f not in assigned]
        # Critical orphans: ignore tests, docs, root-configs, etc.
        critical_orphans = [f for f in orphans if _is_high_priority_file(f)]
        if critical_orphans:
            sample = critical_orphans[:3]
            raise ValueError(
                f"VALIDATION_FAILURE: {len(critical_orphans)} core source files "
                f"are missing from the wiki plan. Example missing files: {sample}. "
                "Every core source file must be assigned to a primary page."
            )
        elif orphans:
            logger.info(
                "Skipping %d low-priority files (tests/configs/etc.) from the wiki",
                len(orphans),
            )
```

Replace with a single info log (so the operator can see unselected files):

```python
    # Log unselected files (selection model: not all files need to be on a page)
    if all_files:
        selected = {f for page in pages for f in page.files}
        selected |= {f for page in pages for f in page.secondary_files}
        unselected = [f for f in all_files if f not in selected]
        if unselected:
            logger.info(
                "%d files not selected by any page (expected in selection model)",
                len(unselected),
            )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_wiki_planner.py::test_validate_selections_passes_normal tests/test_wiki_planner.py::test_validate_selections_fails_over_max tests/test_wiki_planner.py::test_validate_selections_fails_empty_leaf_page tests/test_wiki_planner.py::test_validate_selections_allows_empty_parent tests/test_wiki_planner.py::test_validate_wiki_plan_no_orphan_check -v 2>&1 | tail -20
```
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/test_wiki_planner.py
git commit -m "refactor(planner): replace _validate_assignments with _validate_selections; remove orphan enforcement"
```

---

## Task 7: Wire everything together in `generate_wiki_plan`

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — `generate_wiki_plan` function (lines ~1430–1635)

- [ ] **Step 1: Update Phase 2 call in `generate_wiki_plan`**

In `generate_wiki_plan`, replace the Phase 2 block (the `_assign_files` call and its exception handler, approximately lines 1570–1600):

**Old:**
```python
    # Phase 2: Assign files + validate assignments (fast_llm for classification task)
    try:
        primary_assignments, secondary_assignments = await _assign_files(
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
    except Exception as exc:
        partial = None
        if isinstance(exc, ValueError) and len(exc.args) == 3:
            partial = exc.args[2]

        recovery_type = "partial heuristic" if partial else "full heuristic"
        logger.warning(
            "Phase 2 LLM assignment failed: %s — falling back to %s recovery",
            exc,
            recovery_type,
        )
        primary_assignments = _heuristic_recovery_assignment(
            outline, all_files, partial
        )
        secondary_assignments = {p["title"]: [] for p in outline}
```

**New:**
```python
    # Phase 2: Select representative files per page (fast_llm for selection task)
    try:
        primary_selections = await _select_files(
            outline=outline,
            file_summary=file_summary,
            dep_info=dep_info,
            all_files=all_files,
            file_infos=file_analysis.files,
            dep_graph=dep_graph,
            llm=llm,
            system=system,
            on_retry=on_retry,
            max_retries=max_retries,
            fast_llm=fast_llm,
        )
    except Exception as exc:
        partial = None
        if isinstance(exc, ValueError) and len(exc.args) == 3:
            partial = exc.args[2]

        recovery_type = "partial heuristic" if partial else "full heuristic"
        logger.warning(
            "Phase 2 LLM selection failed: %s — falling back to %s recovery",
            exc,
            recovery_type,
        )
        primary_selections = _heuristic_select_files(
            outline, all_files, file_analysis.files, dep_graph, partial
        )
```

- [ ] **Step 2: Update `fixture_recorder` call and final plan assembly**

Replace `fixture_recorder.record_assignments(primary_assignments, secondary_assignments)`:
```python
    if fixture_recorder is not None:
        await fixture_recorder.record_assignments(primary_selections, {})
```

Replace the `raw` dict assembly block:

**Old:**
```python
    raw = {
        "pages": [
            {
                "title": p["title"],
                "purpose": p["purpose"],
                "parent": p.get("parent"),
                "files": primary_assignments.get(p["title"], []),
                "secondary_files": secondary_assignments.get(p["title"], []),
            }
            for p in outline
        ]
    }
```

**New:**
```python
    raw = {
        "pages": [
            {
                "title": p["title"],
                "purpose": p["purpose"],
                "parent": p.get("parent"),
                "files": primary_selections.get(p["title"], []),
                "secondary_files": [],
            }
            for p in outline
        ]
    }
```

- [ ] **Step 3: Set `all_repo_files` on the plan before returning**

After `plan = validate_wiki_plan(...)` and before `if fixture_recorder is not None`:

```python
        plan.all_repo_files = list(all_files)
```

So the block becomes:
```python
    try:
        plan = validate_wiki_plan(
            raw,
            all_files=all_files,
            existing_titles=existing_titles,
            clusters=clusters,
            page_range=page_range,
        )
        plan.all_repo_files = list(all_files)
        if fixture_recorder is not None:
            await fixture_recorder.record_wiki_plan(plan.to_internal_json())
        return plan
    except ValueError as exc:
        ...
```

- [ ] **Step 4: Verify ruff passes**

```bash
uv run ruff check worker/pipeline/wiki_planner.py
uv run ruff format --check worker/pipeline/wiki_planner.py
```

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py
git commit -m "feat(planner): wire _select_files into generate_wiki_plan; set all_repo_files on plan"
```

---

## Task 8: `WikiPlan.all_repo_files` — dataclass and serialization

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — `WikiPlan` dataclass and `to_internal_json`
- Test: `tests/test_wiki_planner.py`

- [ ] **Step 1: Write failing serialization test**

Add to `tests/test_wiki_planner.py`:

```python
def test_wiki_plan_all_repo_files_roundtrip():
    plan = WikiPlan(
        pages=[WikiPageSpec(title="Overview", purpose="Top level.", files=["main.py"])],
        all_repo_files=["main.py", "worker/core.py", "tests/test_core.py"],
    )
    data = plan.to_internal_json()
    assert data["all_repo_files"] == ["main.py", "worker/core.py", "tests/test_core.py"]
    # wiki.json (user-facing) should NOT include all_repo_files
    wiki_data = plan.to_wiki_json()
    assert "all_repo_files" not in wiki_data
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_wiki_planner.py::test_wiki_plan_all_repo_files_roundtrip -v 2>&1 | tail -10
```
Expected: `TypeError` — `WikiPlan` does not accept `all_repo_files`.

- [ ] **Step 3: Add `all_repo_files` field to `WikiPlan`**

In the `WikiPlan` dataclass (around line 166), add after `pages`:

```python
    all_repo_files: list[str] = field(default_factory=list)
    """Snapshot of every file in the repository at planning time.
    
    Used by incremental refresh to detect added/removed files without
    deriving the set from page.files (which now contains only 5–10 selected files).
    Not included in the user-facing wiki.json.
    """
```

- [ ] **Step 4: Update `to_internal_json` to include `all_repo_files`**

In `to_internal_json`, add `"all_repo_files": self.all_repo_files` to the returned dict:

```python
    def to_internal_json(self) -> dict:
        return {
            "repo_notes": self.repo_notes,
            "all_repo_files": self.all_repo_files,
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
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_wiki_planner.py::test_wiki_plan_all_repo_files_roundtrip -v 2>&1 | tail -10
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/test_wiki_planner.py
git commit -m "feat(planner): add WikiPlan.all_repo_files for refresh coverage; include in internal JSON"
```

---

## Task 9: `jobs.py` — refresh comparison fix

**Files:**
- Modify: `worker/jobs.py` lines 833–848 (old_plan loading) and 932–935 (added/removed detection)
- Test: `tests/test_jobs.py` (or `tests/test_refresh.py` — wherever incremental refresh is tested)

- [ ] **Step 1: Write failing test**

Find the test file that covers `run_incremental_refresh` or the `old_all_files` computation. Add:

```python
from worker.pipeline.wiki_planner import WikiPlan, WikiPageSpec


def test_refresh_uses_all_repo_files_not_page_files():
    """all_repo_files from the plan should be used for added-file detection."""
    # Old plan: 2 pages with 2 selected files each, but 10 total repo files
    old_plan = WikiPlan(
        pages=[
            WikiPageSpec(title="API", purpose=".", files=["api/routes.py", "api/models.py"]),
            WikiPageSpec(title="DB", purpose=".", files=["db/session.py", "db/models.py"]),
        ],
        all_repo_files=[
            "api/routes.py", "api/models.py", "api/middleware.py",
            "db/session.py", "db/models.py", "db/migrations.py",
            "worker/job.py", "worker/queue.py", "main.py", "config.py",
        ],
    )
    # New analysis has 1 new file
    new_all_files = set(old_plan.all_repo_files) | {"api/auth.py"}

    old_all_files = (
        set(old_plan.all_repo_files)
        if old_plan.all_repo_files
        else {f for p in old_plan.pages for f in (p.files or [])}
    )
    added_files = new_all_files - old_all_files
    assert added_files == {"api/auth.py"}
    # Without all_repo_files, added_files would be 6 false positives
    derived = {f for p in old_plan.pages for f in (p.files or [])}
    false_added = new_all_files - derived
    assert len(false_added) == 7  # 6 previously-unselected + 1 genuinely new
```

- [ ] **Step 2: Run test to verify the logic (it tests the fix logic, not the production code directly)**

```bash
uv run pytest tests/ -k "test_refresh_uses_all_repo_files" -v 2>&1 | tail -15
```
Expected: PASS (this test validates the logic we're about to apply).

- [ ] **Step 3: Update `old_plan` loading in `jobs.py`**

In `jobs.py`, the `old_plan = WikiPlan(...)` construction (lines 833–849), add `all_repo_files`:

```python
        old_plan = WikiPlan(
            repo_notes=(
                saved_repo_notes or plan_data.get("repo_notes", [{"content": ""}])
            ),
            pages=[
                WikiPageSpec(
                    title=p["title"],
                    purpose=p.get("purpose", ""),
                    parent=p.get("parent"),
                    files=p.get("files", []),
                    secondary_files=p.get("secondary_files", []),
                    page_notes=saved_page_notes.get(p["title"], [{"content": ""}]),
                )
                for p in plan_data.get("pages", [])
            ],
            all_repo_files=plan_data.get("all_repo_files", []),
        )
```

- [ ] **Step 4: Update the added/removed file detection in `jobs.py`**

Replace lines 932–935:

**Old:**
```python
        old_all_files = {f for p in old_plan.pages for f in (p.files or [])}
        new_all_files = set(file_analysis.files.keys())
        added_files = new_all_files - old_all_files
        removed_files = old_all_files - new_all_files
```

**New:**
```python
        # Use the repo-level snapshot if available; fall back to page-file union
        # for plans generated before this change.
        old_all_files = (
            set(old_plan.all_repo_files)
            if old_plan.all_repo_files
            else {f for p in old_plan.pages for f in (p.files or [])}
        )
        new_all_files = set(file_analysis.files.keys())
        added_files = new_all_files - old_all_files
        removed_files = old_all_files - new_all_files
```

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ --ignore=tests/e2e -x -q 2>&1 | tail -30
```
Expected: all tests pass (or only pre-existing failures unrelated to this change).

- [ ] **Step 6: Commit**

```bash
git add worker/jobs.py tests/
git commit -m "fix(refresh): use WikiPlan.all_repo_files for added/removed file detection"
```

---

## Task 10: Full test suite and linting pass

**Files:**
- `tests/test_wiki_planner.py` — integration-level test for the full two-phase planner
- `worker/pipeline/wiki_planner.py`, `worker/jobs.py` — linting

- [ ] **Step 1: Write integration test for `generate_wiki_plan` with mock LLM**

Add to `tests/test_wiki_planner.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from worker.pipeline.wiki_planner import generate_wiki_plan, WikiPlan
from worker.pipeline.ast_analysis import FileAnalysis, FileInfo


def _make_file_analysis(files: dict[str, list[str]]) -> FileAnalysis:
    """Create a minimal FileAnalysis from {path: [entity_names]}."""
    fa = FileAnalysis(files={})
    for path, entities in files.items():
        fa.files[path] = FileInfo(
            path=path,
            entities=[{"name": e, "kind": "function", "line": 1} for e in entities],
            imports=[],
            summary=", ".join(entities),
        )
    return fa


@pytest.mark.asyncio
async def test_generate_wiki_plan_uses_selection_model(mock_llm):
    """Phase 2 should produce selections, not exhaustive assignments."""
    file_analysis = _make_file_analysis(
        {
            "api/routes.py": ["get_user", "create_user"],
            "api/models.py": ["User", "Session"],
            "worker/job.py": ["run_job"],
            "tests/test_api.py": ["test_get_user"],
        }
    )
    # Phase 1 outline response
    outline_response = {
        "pages": [
            {"title": "API", "purpose": "REST endpoints."},
            {"title": "Worker", "purpose": "Background jobs."},
        ]
    }
    # Phase 2 selection response
    selection_response = {
        "selections": [
            {"page_title": "API", "files": ["api/routes.py", "api/models.py"]},
            {"page_title": "Worker", "files": ["worker/job.py"]},
        ]
    }
    mock_llm.generate_structured = AsyncMock(
        side_effect=[outline_response, selection_response]
    )

    plan = await generate_wiki_plan(
        file_analysis=file_analysis,
        repo_name="test-repo",
        llm=mock_llm,
    )

    assert isinstance(plan, WikiPlan)
    api_page = next(p for p in plan.pages if p.title == "API")
    assert "api/routes.py" in api_page.files
    assert "api/models.py" in api_page.files
    # tests/test_api.py should NOT be selected (no enforcement of coverage)
    assert "tests/test_api.py" not in api_page.files
    assert "tests/test_api.py" not in (
        next(p for p in plan.pages if p.title == "Worker").files
    )
    # all_repo_files should contain all analyzed files
    assert set(plan.all_repo_files) == set(file_analysis.files.keys())
```

Note: `mock_llm` fixture is defined in `tests/conftest.py`. Check that it supports `AsyncMock` for `generate_structured`.

- [ ] **Step 2: Run the integration test**

```bash
uv run pytest tests/test_wiki_planner.py::test_generate_wiki_plan_uses_selection_model -v 2>&1 | tail -20
```
Expected: PASS.

- [ ] **Step 3: Run the full planner test suite**

```bash
uv run pytest tests/test_wiki_planner.py -v 2>&1 | tail -40
```
Expected: all tests pass. Fix any failures before proceeding.

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest tests/ --ignore=tests/e2e -q 2>&1 | tail -20
```
Expected: no new failures.

- [ ] **Step 5: Ruff check and format**

```bash
uv run ruff check .
uv run ruff format --check .
```
Fix any issues.

- [ ] **Step 6: Final commit**

```bash
git add -p  # stage any remaining changes
git commit -m "test(planner): add integration test for page-centric selection model"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|---|---|
| Page-centric schema (page → files) | Tasks 1, 3, 4 |
| File count target 5–8, range 3–10 | Tasks 1, 3 (prompt), 6 (validation) |
| Code files preferred over docs | Tasks 2 (scoring), 3 (prompt) |
| Pre-filter candidates per page | Task 2 |
| Heuristic fallback with scoring | Task 5 |
| Remove orphan enforcement | Task 6 |
| `WikiPlan.all_repo_files` | Task 8 |
| `jobs.py` refresh fix | Task 9 |
| `secondary_files` retained (empty) | Task 7 |
| `_build_assignment_prompt` dead code removed | Task 3 |
| RAG retrieval unaffected | No code change needed — FAISS searches full corpus |

### Placeholder scan

No TBDs, TODOs, or "implement later" placeholders.

### Type consistency

- `_score_file_for_page` uses `file_infos: dict[str, Any]` — matches usage in `_prefilter_candidates` and `_heuristic_select_files`
- `_select_files` passes `file_infos=file_analysis.files` — `FileAnalysis.files` is `dict[str, FileInfo]`, compatible with `dict[str, Any]`
- `WikiPlan.all_repo_files: list[str]` — set as `list(all_files)` in Task 7 Step 3, loaded as `plan_data.get("all_repo_files", [])` in Task 9 Step 3
- `_SELECTION_SCHEMA` key `"selections"` — matches `raw.get("selections", [])` in `_select_files_in_batches`
- `_validate_selections` called from `_select_files` — signatures match
- `_heuristic_select_files` returns `dict[str, list[str]]` — matches expected type in `generate_wiki_plan`
