# Wiki Page Generation Quality Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four structural defects in wiki page generation (file ordering, orchestrator under-coverage, sibling content bleed, ASCII-only file scoring) and remove FAISS/embedding dependency from the indexing pipeline.

**Architecture:** Three-PR sequence. **Phase A** patches the planner and prompts (relevance scoring, sibling-aware outlines, scope-disciplined drafts, rank-weighted retrieval/entity quotas, mandatory `en_keywords` for CJK pages, file ownership enforcement, adaptive file budget) and extracts reusable retrieval primitives into `worker/pipeline/retrieval/` + `worker/utils/tokenize.py`. **Phase B1** adds a deterministic BM25 keyword index. **Phase B2** replaces the single-shot draft with `Outline → Skeleton → per-section draft → Stitch`, deletes Stage 4 (FAISS) and `worker/pipeline/rag_indexer.py` outright, removes `EmbeddingProvider` from every indexing call site, and temporarily disables Deep Research with HTTP 503 / CLI exit / frontend hide.

**Tech Stack:** Python 3.12, FastAPI, ARQ, pydantic-settings v2, SQLAlchemy 2.0 async, `rank_bm25` (new — pure Python), pytest with `asyncio_mode="auto"`. Source spec: `docs/spec/claude/2026-04-29-wiki-page-quality-redesign.md`.

**Source spec citations:** Every task references the spec ID (A1–A15, B1–B5) so reviewers can trace each change back to its design rationale. When the spec already shows pseudocode (e.g. the rank-weighted quota formula), this plan reproduces it verbatim rather than re-deriving it.

---

## File Structure

### New files (Phase A)

| Path | Responsibility |
|---|---|
| `worker/utils/tokenize.py` | CJK + ASCII tokenizer (`tokenize_text`). Single source of truth for tokenization across wiki + fast-report (A7). |
| `worker/pipeline/retrieval/__init__.py` | New retrieval sub-package marker (A11/A12/A13). |
| `worker/pipeline/retrieval/repo_index.py` | Builds the unified per-repo retrieval index (renamed from `worker/pipeline/fast_report_index.py`) (A11). |
| `worker/pipeline/retrieval/repo_index_io.py` | Loader/validator helpers for `repo_index.json` shared by wiki and fast-report (A11). |
| `worker/pipeline/retrieval/repo_search.py` | Domain-agnostic primitives extracted from `worker/fast_report/search.py`: `score_file_for_query`, `expand_candidate_paths`, `build_slice_candidates`, `apply_token_budget`, `neighbors_for_graph`, `RankedFile`, `ScoredEntity`, `SliceCandidate`, plus a profile-keyed `expansion_graph_for(profile)` (A12). |
| `worker/pipeline/retrieval/code_slices.py` | Source-window extractor (`extract_source_slice`), moved from `worker/fast_report/slices.py` (A13). |
| `tests/worker/test_repo_index.py` | New — covers artifact rename and on-disk migration. |
| `tests/worker/test_repo_search.py` | New — covers extracted retrieval primitives. |
| `tests/worker/test_index_artifacts.py` | New — asserts `ast/` contains exactly `repo_index.json` + `wiki_plan.json` after a fresh index. |
| `tests/worker/test_pipeline_layout.py` | New — asserts the new sub-package layout, back-compat re-exports, and deprecation-shim warnings (A15). |
| `tests/worker/test_tokenize.py` | New — CJK runs, mixed CJK+ASCII, camel case, path segments. |

### Renamed/moved files (Phase A — sub-package restructure A15)

| Before | After |
|---|---|
| `worker/pipeline/page_generator.py` | `worker/pipeline/page/generator.py` |
| `worker/pipeline/page_outline.py` | `worker/pipeline/page/outline.py` |
| `worker/pipeline/page_draft.py` | `worker/pipeline/page/draft.py` (used internally by `outline.py` for now; absorbed into `section_drafter.py` in B2) |
| `worker/pipeline/page_formatters.py` | `worker/pipeline/page/formatters.py` |
| `worker/pipeline/fact_check.py` | `worker/pipeline/page/fact_check.py` |
| `worker/pipeline/diagram_post_processor.py` | `worker/pipeline/page/diagram_post_processor.py` |
| `worker/pipeline/wiki_planner.py` | `worker/pipeline/planner/wiki_planner.py` |
| `worker/pipeline/outline_anchors.py` | `worker/pipeline/planner/outline_anchors.py` |
| `worker/pipeline/user_steering.py` | `worker/pipeline/planner/user_steering.py` |
| `worker/pipeline/fast_report_index.py` | thin deprecation shim re-exporting from `worker/pipeline/retrieval/repo_index.py` |
| `worker/fast_report/slices.py` | thin deprecation shim re-exporting from `worker/pipeline/retrieval/code_slices.py` |
| Top-level (kept) | `ingestion.py`, `ast_analysis.py`, `dependency_graph.py`, `language.py`, `pipeline_logging.py`, `__init__.py` |

`worker/pipeline/__init__.py` re-exports `WikiPlanner`, `WikiPageSpec`, `WikiPlan`, `generate_page`, `generate_page_batch`, `compute_generation_order`, `PageResult`, `validate_outline`, `PageOutline`, `SectionPlan`, `DiagramPlan`, `FileAnalysis`, `DependencyGraph` from their new locations.

### New files (Phase B1)

| Path | Responsibility |
|---|---|
| `worker/pipeline/retrieval/keyword_index.py` | `KeywordIndex` — BM25 over chunks with per-file quotas. Replaces `FAISSStore.multi_search` for wiki retrieval. |
| `tests/worker/test_keyword_index.py` | Recall parity vs FAISS (run before FAISS deletion). |

### New files (Phase B2)

| Path | Responsibility |
|---|---|
| `worker/pipeline/page/section_drafter.py` | Pass 2a (Skeleton) + Pass 2b (per-section draft) + Pass 2c (Stitch). Replaces `page_draft.py`. |
| `tests/worker/test_section_drafter.py` | Section-level retrieval scoping; out-of-scope-claim handling. |

### Deleted files (Phase B2)

- `worker/pipeline/rag_indexer.py` — Stage 4 deleted (B4).
- `worker/pipeline/page_draft.py` content — absorbed into `section_drafter.py`.

---

## Phase A — Planner & Prompt Patches (PR `feat/wiki-quality-layer-a`)

PR #40 must be merged before Phase A. Phase A's budgets assume PR #40's raised caps.

### Task A.0: Create the worktree and base branch

**Files:**
- Worktree: `~/code/AutoWiki-worktrees/wiki-quality-layer-a`

- [ ] **Step 1: Create worktree off main**

```bash
cd /Users/lazyxiang/code/AutoWiki
git fetch origin main
git worktree add -b feat/wiki-quality-layer-a ~/code/AutoWiki-worktrees/wiki-quality-layer-a origin/main
cd ~/code/AutoWiki-worktrees/wiki-quality-layer-a
uv sync
```

Expected: clean worktree, all tests passing baseline.

- [ ] **Step 2: Capture pre-change baseline**

```bash
uv run pytest tests/ --ignore=tests/e2e -q 2>&1 | tail -5
```

Record: passing count, failing count. Phase A must end at the same numbers (plus new tests).

---

### Task A.7a: Extract `tokenize_text` to `worker/utils/tokenize.py` (A7)

**Files:**
- Create: `worker/utils/tokenize.py`
- Test: `tests/worker/test_tokenize.py`

> **Why first:** Tokenizer is the prerequisite for A8 (`en_keywords` scoring), A11/A12 (retrieval primitives expect a shared tokenizer), and B1 (`KeywordIndex` builds its IDF table via `tokenize_text`). All later tasks import from this module.

- [ ] **Step 1: Write the failing test**

Create `tests/worker/test_tokenize.py`:

```python
from worker.utils.tokenize import tokenize_text


def test_ascii_lowercased_and_min_length():
    assert tokenize_text("Hello WORLD ab a1") == {"hello", "world"}


def test_camel_and_snake_split():
    assert {"wiki", "planner"} <= tokenize_text("WikiPlanner wiki_planner")


def test_path_segments_split():
    assert {"web", "components", "wiki", "page"} <= tokenize_text(
        "web/components/WikiPage.tsx"
    )


def test_cjk_runs_extracted():
    tokens = tokenize_text("依赖图谱构建")
    assert "依赖图谱构建" in tokens or {"依赖", "图谱", "构建"} & tokens


def test_cjk_mixed_with_ascii():
    tokens = tokenize_text("前端 web app 路由")
    assert "web" in tokens and "app" in tokens
    assert any("前" in t or "路" in t for t in tokens)


def test_no_short_tokens():
    assert "a" not in tokenize_text("a ab abc")
    assert "ab" not in tokenize_text("ab abc")
```

Run:

```bash
uv run pytest tests/worker/test_tokenize.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 2: Implement `tokenize_text`**

Create `worker/utils/tokenize.py` by lifting the body of `_tokenize` from `worker/fast_report/search.py:943` (which is the more sophisticated of the two duplicates). Promote it to public, add CJK support:

```python
"""Shared tokenizer used by retrieval, planner scoring, and search.

Domain-neutral: lowercased ASCII tokens (>= 3 chars) plus CJK runs. Used by
``KeywordIndex`` (Phase B1), ``_score_file_for_page`` (Phase A8), and the
fast-report retrieval primitives.
"""

from __future__ import annotations

import re

_ASCII_TOKEN = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[㐀-鿿豈-﫿]+")
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def tokenize_text(text: str) -> set[str]:
    if not text:
        return set()
    normalized = _CAMEL_SPLIT.sub(" ", text)
    normalized = re.sub(r"[/_\-.]", " ", normalized)
    tokens: set[str] = {
        w for w in _ASCII_TOKEN.findall(normalized.lower()) if len(w) >= 3
    }
    for run in _CJK_RUN.findall(text):
        tokens.add(run)
        # also expose 2-char bigrams to handle compound terms ("依赖图谱" -> "依赖", "图谱")
        for i in range(0, len(run) - 1, 2):
            tokens.add(run[i : i + 2])
    return tokens
```

Run:

```bash
uv run pytest tests/worker/test_tokenize.py -v
```

Expected: all six tests PASS.

- [ ] **Step 3: Commit**

```bash
git add worker/utils/tokenize.py tests/worker/test_tokenize.py
git commit -m "feat(utils): extract shared tokenize_text with CJK support (A7)"
```

---

### Task A.7b: Replace duplicate `_tokenize` definitions with the shared helper (A7)

**Files:**
- Modify: `worker/fast_report/search.py:943` — replace `_tokenize` definition with `from worker.utils.tokenize import tokenize_text as _tokenize`.
- Modify: `worker/pipeline/wiki_planner.py:733-735` — same replacement.
- Modify: `worker/pipeline/wiki_planner.py:_score_file_for_page` (the inline tokenization at 775).

- [ ] **Step 1: Update `worker/fast_report/search.py`**

Delete the local definition at line 943; add at top of file:

```python
from worker.utils.tokenize import tokenize_text as _tokenize  # noqa: F401
```

Keep the alias `_tokenize` so existing call sites compile.

- [ ] **Step 2: Update `worker/pipeline/wiki_planner.py`**

Replace lines 733–735:

```python
from worker.utils.tokenize import tokenize_text as _tokenize
```

(Keep the alias; many call sites still use `_tokenize` directly.)

- [ ] **Step 3: Run existing tests for behavior preservation**

```bash
uv run pytest tests/worker/test_fast_report_search_adaptive.py tests/worker/test_wiki_planner.py -v
```

Expected: PASS — no behavior changes for ASCII inputs (the CJK addition is additive). If any test broke on tokenizer specifics, the assertion was overfit; relax it to `>=` or `<=` over the relevant token set.

- [ ] **Step 4: Audit other call sites**

```bash
grep -rn "_tokenize\b" worker/ tests/ | grep -v "tokenize_text"
```

Migrate any remaining direct references.

- [ ] **Step 5: Commit**

```bash
git add worker/
git commit -m "refactor(pipeline,fast_report): consolidate _tokenize on tokenize_text (A7)"
```

---

### Task A.11: Rename `fast_report_index` → `repo_index` with on-disk migration (A11)

**Files:**
- Create: `worker/pipeline/retrieval/__init__.py` (empty)
- Create: `worker/pipeline/retrieval/repo_index.py` (was `worker/pipeline/fast_report_index.py`)
- Create: `worker/pipeline/retrieval/repo_index_io.py` (loader/validator helpers from `worker/fast_report/jobs.py:93-141`)
- Modify: `worker/pipeline/fast_report_index.py` → deprecation shim
- Modify: `worker/fast_report/jobs.py:93-141` — call into shared loader
- Modify: `worker/index/full.py:40,298,307` — import + builder + artifact path
- Modify: `worker/index/refresh.py:33,462,470` — same
- Test: `tests/worker/test_repo_index.py`

> **Why now:** Phase B1's `KeywordIndex` consumes `repo_index.json`. The artifact name and module path must be neutral before B1 lands. The on-disk migration prevents any user with an existing index from having to re-index from scratch.

- [ ] **Step 1: Write the failing migration test**

Create `tests/worker/test_repo_index.py`:

```python
import json
import warnings
from pathlib import Path

import pytest

from worker.pipeline.retrieval import repo_index_io


def test_loader_renames_legacy_artifact(tmp_path: Path):
    ast_dir = tmp_path / "ast"
    ast_dir.mkdir()
    legacy = ast_dir / "fast_report_index.json"
    legacy.write_text(json.dumps({"index_version": 2, "files": []}))

    loaded = repo_index_io.load_repo_index(tmp_path)

    assert loaded["index_version"] == 2
    assert (ast_dir / "repo_index.json").exists()
    assert not legacy.exists()


def test_loader_raises_when_both_missing(tmp_path: Path):
    (tmp_path / "ast").mkdir()
    with pytest.raises(repo_index_io.RepoIndexMissingError):
        repo_index_io.load_repo_index(tmp_path)


def test_legacy_module_emits_deprecation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from worker.pipeline import fast_report_index  # noqa: F401

        from worker.pipeline.fast_report_index import build_fast_report_index  # noqa: F401

    messages = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("repo_index" in m for m in messages)
```

Run:

```bash
uv run pytest tests/worker/test_repo_index.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 2: Move the index builder**

```bash
mkdir -p worker/pipeline/retrieval
touch worker/pipeline/retrieval/__init__.py
git mv worker/pipeline/fast_report_index.py worker/pipeline/retrieval/repo_index.py
```

In `worker/pipeline/retrieval/repo_index.py`:

- Rename `INDEX_VERSION` → `REPO_INDEX_VERSION`.
- Rename `build_fast_report_index` → `build_repo_index`.
- Replace the literal `"index_version": INDEX_VERSION` with `"index_version": REPO_INDEX_VERSION`.

- [ ] **Step 3: Create the loader/validator module**

Create `worker/pipeline/retrieval/repo_index_io.py` by lifting the helpers from `worker/fast_report/jobs.py:93-141`:

```python
"""Shared loader for the per-repo retrieval index (``repo_index.json``).

Used by:
- ``worker.fast_report.jobs`` (legacy fast-report flow)
- ``worker.pipeline.retrieval.keyword_index`` (Phase B1)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from worker.pipeline.retrieval.repo_index import REPO_INDEX_VERSION

log = logging.getLogger(__name__)

_NEW_NAME = "repo_index.json"
_LEGACY_NAME = "fast_report_index.json"


class RepoIndexMissingError(FileNotFoundError):
    """Neither repo_index.json nor fast_report_index.json exists."""


class RepoIndexOutdatedError(RuntimeError):
    """Loaded repo_index.json is older than REPO_INDEX_VERSION."""


def _ast_dir(repo_data_dir: Path) -> Path:
    return repo_data_dir / "ast"


def load_repo_index(repo_data_dir: Path) -> dict:
    ast_dir = _ast_dir(repo_data_dir)
    new_path = ast_dir / _NEW_NAME
    legacy_path = ast_dir / _LEGACY_NAME

    if not new_path.exists() and legacy_path.exists():
        log.info(
            "renaming legacy fast_report_index.json -> repo_index.json at %s",
            ast_dir,
        )
        legacy_path.rename(new_path)

    if not new_path.exists():
        raise RepoIndexMissingError(
            f"missing {new_path}; rebuild via Stage 2/3"
        )

    data = json.loads(new_path.read_text())
    validate_repo_index_version(data)
    return data


def validate_repo_index_version(data: dict) -> None:
    version = data.get("index_version", 0)
    if version < REPO_INDEX_VERSION:
        raise RepoIndexOutdatedError(
            f"repo_index.json version {version} < expected {REPO_INDEX_VERSION}"
        )
```

- [ ] **Step 4: Update `worker/fast_report/jobs.py:93-141`**

Replace the local `_load_fast_report_index`, `_validate_fast_report_index_version`, and `_FastReportIndexOutdatedError` with imports from `worker.pipeline.retrieval.repo_index_io`. Keep module-local aliases if needed for one release.

- [ ] **Step 5: Update `worker/index/full.py` and `worker/index/refresh.py`**

In both files:

```python
# was:
# from worker.pipeline.fast_report_index import build_fast_report_index
from worker.pipeline.retrieval.repo_index import build_repo_index
```

Update the call site (line 298 in `full.py`, line 462 in `refresh.py`):

```python
fast_report_index = build_repo_index(...)
```

Update artifact path (line 307 in `full.py`, line 470 in `refresh.py`):

```python
ast_dir / "repo_index.json"
```

- [ ] **Step 6: Add the deprecation shim**

Create `worker/pipeline/fast_report_index.py`:

```python
"""Deprecated: import from ``worker.pipeline.retrieval.repo_index`` instead."""

from __future__ import annotations

import warnings

warnings.warn(
    "worker.pipeline.fast_report_index is deprecated; import from "
    "worker.pipeline.retrieval.repo_index instead",
    DeprecationWarning,
    stacklevel=2,
)

from worker.pipeline.retrieval.repo_index import (  # noqa: F401, E402
    REPO_INDEX_VERSION as INDEX_VERSION,
    build_repo_index as build_fast_report_index,
)
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/worker/test_repo_index.py tests/worker/test_fast_report_index.py tests/worker/test_jobs.py -v
```

Expected: all PASS. Existing fast-report tests pass through the shim.

- [ ] **Step 8: Commit**

```bash
git add worker/ tests/worker/test_repo_index.py
git commit -m "refactor(pipeline): rename fast_report_index -> retrieval/repo_index with on-disk migration (A11)"
```

---

### Task A.12: Extract retrieval primitives to `repo_search.py` (A12)

**Files:**
- Create: `worker/pipeline/retrieval/repo_search.py`
- Modify: `worker/fast_report/search.py` — keep only orchestration (`retrieve_code_evidence` + question-type plumbing); import primitives.
- Modify: `worker/fast_report/planning.py` — `expansion_graph_for(question_type)` registers `"how_does_it_work"` etc. via `repo_search.register_expansion_profile`.
- Test: `tests/worker/test_repo_search.py`

- [ ] **Step 1: Audit which symbols are domain-agnostic**

```bash
grep -n "^def \|^class " worker/fast_report/search.py
```

Mark for extraction (per spec §5.2 A12):
- `_score_file_multi_slice` → `score_file_for_query` (public)
- `expand_candidate_paths`
- `build_slice_candidates`
- `apply_token_budget`
- `neighbors_for_graph`
- Dataclasses: `RankedFile`, `ScoredEntity`, `SliceCandidate`

Keep in `worker/fast_report/search.py`:
- `retrieve_code_evidence` (top-level orchestrator, fast-report-specific)
- Any function that reads `plan.question_type` directly.

- [ ] **Step 2: Write the failing test**

Create `tests/worker/test_repo_search.py`:

```python
import importlib

from worker.pipeline.retrieval import repo_search


def test_primitives_are_public():
    for name in (
        "score_file_for_query",
        "expand_candidate_paths",
        "build_slice_candidates",
        "apply_token_budget",
        "neighbors_for_graph",
        "RankedFile",
        "ScoredEntity",
        "SliceCandidate",
        "register_expansion_profile",
        "expansion_graph_for",
    ):
        assert hasattr(repo_search, name), f"missing {name}"


def test_no_circular_import_with_fast_report():
    fr = importlib.import_module("worker.fast_report.search")
    assert hasattr(fr, "retrieve_code_evidence")


def test_expansion_profile_registry():
    repo_search.register_expansion_profile("test_profile", {"hops": 2})
    assert repo_search.expansion_graph_for("test_profile") == {"hops": 2}
```

Run: FAIL.

- [ ] **Step 3: Move primitives**

Create `worker/pipeline/retrieval/repo_search.py` with the listed symbols. For the registry:

```python
_PROFILES: dict[str, dict] = {}


def register_expansion_profile(name: str, config: dict) -> None:
    _PROFILES[name] = config


def expansion_graph_for(name: str) -> dict:
    if name not in _PROFILES:
        raise KeyError(f"unknown expansion profile: {name}")
    return _PROFILES[name]
```

In `worker/fast_report/search.py`:

```python
from worker.pipeline.retrieval.repo_search import (
    RankedFile,
    ScoredEntity,
    SliceCandidate,
    apply_token_budget,
    build_slice_candidates,
    expand_candidate_paths,
    expansion_graph_for,
    neighbors_for_graph,
    register_expansion_profile,
    score_file_for_query,
)
```

In `worker/fast_report/planning.py`, register fast-report profiles at module scope:

```python
register_expansion_profile("how_does_it_work", {...})
register_expansion_profile("what_is_it", {...})
# etc.
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/worker/test_repo_search.py tests/worker/test_fast_report_search_adaptive.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/ tests/worker/test_repo_search.py
git commit -m "refactor(retrieval): extract domain-agnostic primitives to repo_search (A12)"
```

---

### Task A.13: Move `slices.py` → `code_slices.py` (A13)

**Files:**
- Move: `worker/fast_report/slices.py` → `worker/pipeline/retrieval/code_slices.py`
- Modify: `worker/fast_report/slices.py` → deprecation shim
- Modify: every consumer of `worker.fast_report.slices.extract_source_slice` → import from new path

- [ ] **Step 1: Find consumers**

```bash
grep -rn "from worker.fast_report.slices\|worker.fast_report.slices" worker/ tests/
```

- [ ] **Step 2: Move the module**

```bash
git mv worker/fast_report/slices.py worker/pipeline/retrieval/code_slices.py
```

- [ ] **Step 3: Update consumers**

Replace each import with `from worker.pipeline.retrieval.code_slices import extract_source_slice` (etc.).

- [ ] **Step 4: Add deprecation shim**

Create `worker/fast_report/slices.py`:

```python
"""Deprecated: import from ``worker.pipeline.retrieval.code_slices`` instead."""

from __future__ import annotations

import warnings

warnings.warn(
    "worker.fast_report.slices is deprecated; import from "
    "worker.pipeline.retrieval.code_slices instead",
    DeprecationWarning,
    stacklevel=2,
)

from worker.pipeline.retrieval.code_slices import *  # noqa: F401, F403, E402
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/worker/test_fast_report_slices.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add worker/ tests/
git commit -m "refactor(retrieval): move slices to retrieval/code_slices (A13)"
```

---

### Task A.14: Stop persisting `ast/file_analysis_summary.txt` (A14)

**Files:**
- Modify: `worker/index/full.py:277` — remove the write.
- Modify: `worker/index/refresh.py:400` — remove the write.
- Test: `tests/worker/test_index_artifacts.py`

> **Why this is safe:** `grep -rn "file_analysis_summary"` returns hits only at the two write call sites — nothing reads it back. The in-memory `FileAnalysis.to_llm_summary()` call inside `wiki_planner._build_outline_prompt` is unchanged.

- [ ] **Step 1: Verify no readers**

```bash
grep -rn "file_analysis_summary" worker/ api/ cli/ tests/
```

Expected: hits only at `worker/index/full.py:277` and `worker/index/refresh.py:400`. If any test or runtime code reads the file, abort and re-evaluate.

- [ ] **Step 2: Write the failing test**

Create `tests/worker/test_index_artifacts.py`:

```python
from pathlib import Path


def test_ast_dir_contains_only_repo_index_and_wiki_plan(simple_repo_indexed: Path):
    """After a fresh full index, ast/ contains exactly two files."""
    ast_dir = simple_repo_indexed / "ast"
    files = sorted(p.name for p in ast_dir.iterdir() if p.is_file())
    assert files == ["repo_index.json", "wiki_plan.json"], files
```

Add a `simple_repo_indexed` fixture to `tests/conftest.py` if not present (run a full mock-LLM index against `tests/fixtures/simple-repo` and yield the repo data dir).

Run:

```bash
uv run pytest tests/worker/test_index_artifacts.py -v
```

Expected: FAIL — `file_analysis_summary.txt` still present.

- [ ] **Step 3: Remove the writes**

In `worker/index/full.py:277` (and `worker/index/refresh.py:400`), delete the block that writes `file_analysis_summary.txt`. Keep the `to_llm_summary()` call that feeds the prompt.

- [ ] **Step 4: Add opt-in prompt dump**

Behind `AUTOWIKI_DEBUG_DUMP_PROMPTS=1`, write the actual Phase-1 prompt to `~/.autowiki/repos/{hash}/logs/phase1_prompt.txt`. Add at the call site of `_build_outline_prompt`:

```python
if os.environ.get("AUTOWIKI_DEBUG_DUMP_PROMPTS") == "1":
    log_dir = repo_data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "phase1_prompt.txt").write_text(prompt_text, encoding="utf-8")
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/worker/test_index_artifacts.py tests/worker/test_jobs.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add worker/index/ tests/worker/test_index_artifacts.py tests/conftest.py
git commit -m "refactor(index): drop file_analysis_summary.txt; add opt-in prompt dump (A14)"
```

---

### Task A.1: Relevance-scored Phase-2 selection schema (A1)

**Files:**
- Modify: `worker/pipeline/wiki_planner.py:334-353` — `_SELECTION_SCHEMA`.
- Modify: `worker/pipeline/wiki_planner.py:355-386` — `_SYSTEM` prompt.
- Modify: `worker/pipeline/wiki_planner.py:_build_selection_user`.
- Modify: `worker/pipeline/wiki_planner.py:_validate_selections` (line 612).
- Modify: `worker/pipeline/wiki_planner.py:_select_files` — unwrap `{path, relevance}` → `list[str]` at the boundary; add ordering check + 1 extra retry budget for ordering violations.
- Test: `tests/worker/test_wiki_planner.py` (extend).

- [ ] **Step 1: Write the failing test**

Add to `tests/worker/test_wiki_planner.py`:

```python
def test_selection_schema_requires_relevance_scores():
    from worker.pipeline.wiki_planner import _SELECTION_SCHEMA

    items = _SELECTION_SCHEMA["properties"]["selections"]["items"]
    files_schema = items["properties"]["files"]
    assert files_schema["type"] == "array"
    file_item = files_schema["items"]
    assert file_item["type"] == "object"
    assert set(file_item["required"]) == {"path", "relevance"}
    assert file_item["properties"]["relevance"]["minimum"] == 1
    assert file_item["properties"]["relevance"]["maximum"] == 10


def test_validate_selections_rejects_increasing_relevance():
    from worker.pipeline.wiki_planner import _validate_selections, WikiPlannerError

    raw = {
        "selections": [
            {
                "title": "X",
                "files": [
                    {"path": "a.py", "relevance": 3},
                    {"path": "b.py", "relevance": 8},  # higher than predecessor
                ],
            }
        ]
    }
    outline = [{"title": "X"}]
    with pytest.raises(WikiPlannerError, match="non-increasing"):
        _validate_selections(raw, outline)


def test_validate_selections_demotes_low_relevance_at_position_zero(monkeypatch):
    from worker.pipeline.wiki_planner import _validate_selections, WikiPlannerError

    raw = {
        "selections": [
            {
                "title": "X",
                "files": [
                    {"path": "a.py", "relevance": 2},  # < 3 in slot 0
                    {"path": "b.py", "relevance": 1},
                ],
            }
        ]
    }
    outline = [{"title": "X"}]
    with pytest.raises(WikiPlannerError):
        _validate_selections(raw, outline)


def test_select_files_unwraps_to_list_of_str(monkeypatch):
    """Downstream WikiPageSpec.files stays list[str]; relevance is internal."""
    # full integration test — see existing _select_files patterns for setup
    ...
```

Run: FAIL.

- [ ] **Step 2: Update `_SELECTION_SCHEMA`**

```python
_SELECTION_SCHEMA = {
    "type": "object",
    "required": ["selections"],
    "properties": {
        "selections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "files"],
                "properties": {
                    "title": {"type": "string"},
                    "files": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "required": ["path", "relevance"],
                            "properties": {
                                "path": {"type": "string"},
                                "relevance": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 10,
                                },
                            },
                        },
                    },
                },
            },
        }
    },
}
```

- [ ] **Step 3: Update `_SYSTEM` and `_build_selection_user`**

In the system prompt at `wiki_planner.py:355-386`, append:

> For each page, return its `files` as an array of `{path, relevance}` objects. `relevance` is an integer 1–10 (10 = directly documents the page; 1 = peripheral). The array MUST be ordered most-relevant-first; relevance scores MUST be non-increasing along the array. The first file is the page's primary subject and must score ≥ 3.

In `_build_selection_user`, restate the rule in the user-prompt section that lists each page.

- [ ] **Step 4: Update `_validate_selections` (line 612)**

Add checks:

```python
for selection in selections:
    files = selection["files"]
    scores = [f["relevance"] for f in files]
    # non-increasing
    if any(scores[i] < scores[i + 1] for i in range(len(scores) - 1)):
        raise WikiPlannerError(
            f"non-increasing relevance violated for page {selection['title']!r}: "
            f"{scores}"
        )
    # position-zero floor
    if scores[0] < 3:
        raise WikiPlannerError(
            f"page {selection['title']!r} has relevance < 3 at position 0"
        )
```

- [ ] **Step 5: Unwrap to `list[str]` at the boundary**

In `_select_files` (around line 920), after validation:

```python
for selection in result["selections"]:
    selection["files"] = [f["path"] for f in selection["files"]]
```

`WikiPageSpec.files` stays `list[str]`. Downstream code is unchanged.

- [ ] **Step 6: Add ordering-violation soft retry + demote-and-log fallback**

Per spec §4.3: if `_validate_selections` raises an ordering violation, do one extra feedback retry. If it still fails, demote the offending file to the end of the list and log `wiki_planner.ordering_demotion`:

```python
from worker.pipeline.pipeline_logging import log_validation_retry

# after final retry fails on ordering only:
for selection in result["selections"]:
    files = selection["files"]
    if files and _score_file_for_page(files[0]["path"], page_dict, file_infos, dep_graph) <= 0:
        demoted = files.pop(0)
        files.append(demoted)
        log_validation_retry(  # or a dedicated log_ordering_demotion helper
            "wiki_planner.ordering_demotion",
            {
                "page": selection["title"],
                "demoted_file": demoted["path"],
                "original_position": 0,
                "score": demoted["relevance"],
            },
        )
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/worker/test_wiki_planner.py tests/worker/test_assign_files_batched.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): emit relevance scores in Phase 2 with ordering validation (A1)"
```

---

### Task A.8: Mandatory `en_keywords` for CJK pages (A8)

**Files:**
- Modify: `worker/pipeline/wiki_planner.py:478-481` — Phase 1 prompt language.
- Modify: `worker/pipeline/wiki_planner.py:_validate_outline_structure` (line 539) — enforce.
- Modify: `worker/pipeline/wiki_planner.py:_score_file_for_page` (line 743) — boost `en_keywords` ↔ path-segment overlap.
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

```python
def test_phase1_rejects_cjk_title_without_en_keywords():
    from worker.pipeline.wiki_planner import _validate_outline_structure, WikiPlannerError

    pages = [{"title": "前端应用架构", "purpose": "介绍前端", "parent": None}]
    with pytest.raises(WikiPlannerError, match="en_keywords"):
        _validate_outline_structure(pages, page_range=(1, 30), total_file_count=50)


def test_phase1_accepts_ascii_title_without_en_keywords():
    from worker.pipeline.wiki_planner import _validate_outline_structure

    pages = [{"title": "Frontend Architecture", "purpose": "intro", "parent": None}]
    _validate_outline_structure(pages, page_range=(1, 30), total_file_count=50)


def test_score_file_for_page_boosts_en_keyword_path_overlap():
    from worker.pipeline.wiki_planner import _score_file_for_page

    page = {"title": "前端", "purpose": "", "en_keywords": ["web", "components"]}
    score_match = _score_file_for_page(
        "web/components/Sidebar.tsx", page, file_infos={}, dep_graph={}
    )
    score_miss = _score_file_for_page(
        "worker/pipeline/wiki_planner.py", page, file_infos={}, dep_graph={}
    )
    assert score_match >= score_miss + 4  # +4 per overlap dominates other signals
```

Run: FAIL.

- [ ] **Step 2: Detect CJK in `_validate_outline_structure`**

```python
import re

_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


# inside _validate_outline_structure, per page:
if _has_cjk(page.get("title", "")) or _has_cjk(page.get("purpose", "")):
    en_keywords = page.get("en_keywords") or []
    if not (3 <= len(en_keywords) <= 8):
        raise WikiPlannerError(
            f"page {page['title']!r} has CJK title/purpose but lacks 3-8 en_keywords"
        )
```

- [ ] **Step 3: Tighten Phase 1 prompt at line 478-481**

Replace the optional language with:

> `en_keywords` (REQUIRED when title or purpose contains non-Latin characters; optional otherwise): list 3–8 English keywords drawn from directory names, module names, or file basenames in the file listing. These keywords are used to match Chinese/Japanese/Korean page titles to English source paths. Examples: `["web", "components", "next"]`, `["api", "routers", "fastapi"]`.

- [ ] **Step 4: Boost `en_keywords` in `_score_file_for_page`**

Locate the existing `en_keywords` handling around line 773. Replace with:

```python
en_kws = page.get("en_keywords") or []
path_segments = [seg for seg in path.lower().replace("\\", "/").split("/") if seg]
path_segments_set = set(path_segments)
file_stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
en_overlap = sum(
    1
    for kw in en_kws
    if kw.lower() in path_segments_set or kw.lower() in file_stem
)
score += 4 * en_overlap  # dominates the +0.5 general token overlap
```

- [ ] **Step 5: Add Phase-1 retry telemetry**

In the Phase-1 retry loop (line 683), when `_validate_outline_structure` raises with `en_keywords` in the message, log `wiki_planner.en_keywords_required` via `pipeline_logging.log_validation_retry`.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/worker/test_wiki_planner.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): make en_keywords mandatory for CJK pages and boost in scoring (A8)"
```

---

### Task A.9: Ownership enforcement across siblings (A9)

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — add `_enforce_ownership` and call after `_validate_selections`.
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

```python
def test_enforce_ownership_demotes_lower_scoring_sibling():
    from worker.pipeline.wiki_planner import _enforce_ownership

    outline = [
        {"title": "内容生成引擎", "parent": "core"},
        {"title": "质量校验与修订", "parent": "core"},
    ]
    selections = [
        {"title": "内容生成引擎", "files": ["page_outline.py", "page_generator.py"]},
        {"title": "质量校验与修订", "files": ["page_outline.py", "fact_check.py"]},
    ]
    file_infos = {
        "page_outline.py": _stub_file_info(entity_count=20),
        "page_generator.py": _stub_file_info(entity_count=15),
        "fact_check.py": _stub_file_info(entity_count=10),
    }
    dep_graph = {}

    _enforce_ownership(selections, outline, dep_graph, file_infos)

    owners = [s["files"] for s in selections]
    # page_outline.py owned by exactly one sibling
    co_owners = [s["title"] for s in selections if "page_outline.py" in s["files"]]
    assert len(co_owners) == 1


def test_enforce_ownership_allows_two_non_sibling_owners():
    """Architectural hubs can be cited by two non-sibling pages."""
    from worker.pipeline.wiki_planner import _enforce_ownership

    outline = [
        {"title": "A", "parent": "p1"},
        {"title": "B", "parent": "p2"},  # different parent
    ]
    selections = [
        {"title": "A", "files": ["models.py"]},
        {"title": "B", "files": ["models.py"]},
    ]
    _enforce_ownership(selections, outline, dep_graph={}, file_infos={"models.py": _stub_file_info()})
    co_owners = [s["title"] for s in selections if "models.py" in s["files"]]
    assert len(co_owners) == 2  # allowed
```

Run: FAIL.

- [ ] **Step 2: Implement `_enforce_ownership`**

```python
from collections import defaultdict
from worker.pipeline.pipeline_logging import log_validation_retry


def _enforce_ownership(
    selections: list[dict],
    outline: list[dict],
    dep_graph: dict,
    file_infos: dict,
) -> None:
    """Mutate ``selections`` so each file is owned by at most:
    - 1 sibling page (siblings share a parent), or
    - 2 non-sibling pages (architectural hubs), or
    - top-decile in-degree files: unlimited (hubs).
    """
    title_to_parent = {p["title"]: p.get("parent") for p in outline}
    title_to_selection = {s["title"]: s for s in selections}

    file_to_owners: dict[str, list[str]] = defaultdict(list)
    for s in selections:
        for f in s["files"]:
            file_to_owners[f].append(s["title"])

    hub_files = _compute_hub_modules(dep_graph)  # existing helper

    for path, owner_titles in file_to_owners.items():
        if len(owner_titles) <= 1 or path in hub_files:
            continue

        # group by parent
        by_parent: dict[str | None, list[str]] = defaultdict(list)
        for title in owner_titles:
            by_parent[title_to_parent.get(title)].append(title)

        # within each parent group, keep only the highest-scoring owner
        for parent, group in by_parent.items():
            if len(group) <= 1:
                continue
            scored = [
                (title, _score_file_for_page(
                    path,
                    {"title": title, "purpose": next(p["purpose"] for p in outline if p["title"] == title), "en_keywords": next((p.get("en_keywords") or []) for p in outline if p["title"] == title)},
                    file_infos,
                    dep_graph,
                ))
                for title in group
            ]
            scored.sort(key=lambda x: x[1], reverse=True)
            primary = scored[0][0]
            for title, score in scored[1:]:
                title_to_selection[title]["files"] = [
                    f for f in title_to_selection[title]["files"] if f != path
                ]
                log_validation_retry(
                    "wiki_planner.ownership_demotion",
                    {
                        "file": path,
                        "demoted_page": title,
                        "primary_page": primary,
                        "score_delta": scored[0][1] - score,
                    },
                )

        # cap non-sibling owners at 2
        remaining = [t for t in owner_titles if path in title_to_selection[t]["files"]]
        if len(remaining) > 2:
            scored = [
                (title, _score_file_for_page(path, {"title": title, "purpose": "", "en_keywords": []}, file_infos, dep_graph))
                for title in remaining
            ]
            scored.sort(key=lambda x: x[1], reverse=True)
            for title, _ in scored[2:]:
                title_to_selection[title]["files"] = [
                    f for f in title_to_selection[title]["files"] if f != path
                ]
```

- [ ] **Step 3: Add the feature flag**

Per spec §6.3, gate by `AUTOWIKI_PLANNER_OWNERSHIP=enforce|advise|off` (default `advise`):

```python
import os

mode = os.environ.get("AUTOWIKI_PLANNER_OWNERSHIP", "advise")
if mode != "off":
    _enforce_ownership(selections, outline, dep_graph, file_infos)
    # in "advise" mode, demote-and-log, identical to enforce; flag exists so a
    # follow-up release can flip default to "enforce" without code change.
    # If "advise" should be log-only (no mutation), pass an `apply=False` flag here.
```

For Phase A, treat `advise` and `enforce` identically (both demote-and-log). The flag exists for safe rollout; default flips to `enforce` in a follow-up release.

- [ ] **Step 4: Cap total assignments**

Per spec §5.2 A9:

```python
total_assignments = sum(len(s["files"]) for s in selections)
cap = int(1.5 * len(all_repo_files))
if total_assignments > cap:
    # demote lowest-scored entries from longest selections until under cap
    ...
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/worker/test_wiki_planner.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): enforce file ownership across sibling pages (A9)"
```

---

### Task A.10: Adaptive per-page file budget (A10)

**Files:**
- Modify: `worker/pipeline/wiki_planner.py:_select_files`, `_validate_selections`.
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

```python
def test_n_target_clamps_between_2_and_8():
    from worker.pipeline.wiki_planner import _compute_n_target

    # narrow topic: low median score
    assert _compute_n_target(median_score=1.0, score_threshold=2.0) == 2
    # broad topic: high median score
    assert _compute_n_target(median_score=20.0, score_threshold=2.0) == 8
    # clamp lower bound
    assert _compute_n_target(median_score=0.0, score_threshold=2.0) == 2
```

Run: FAIL.

- [ ] **Step 2: Implement `_compute_n_target`**

```python
import math


def _compute_n_target(median_score: float, score_threshold: float = 2.0) -> int:
    if score_threshold <= 0:
        return 5
    n = max(2, math.ceil(median_score / score_threshold))
    return min(n, 8)
```

In `_select_files`, compute `n_target` from the prefilter score distribution per page and pass into the prompt. Update `_validate_selections` to accept `n_target` per page and require `2 <= len(files) <= max(n_target + 2, 10)` (hard upper bound 10).

- [ ] **Step 3: Run tests + commit**

```bash
uv run pytest tests/worker/test_wiki_planner.py -v
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): adaptive per-page file budget (A10)"
```

---

### Task A.2: Sibling-aware outline prompt (A2)

**Files:**
- Modify: `worker/pipeline/page_outline.py:224-254` — `generate_page_outline` signature.
- Modify: `worker/pipeline/page_generator.py:266-274` — derive sibling info, pass through.
- Test: `tests/worker/test_page_outline.py`

- [ ] **Step 1: Write the failing test**

```python
def test_outline_prompt_includes_sibling_titles_and_out_of_scope(monkeypatch):
    from worker.pipeline.page_outline import _build_outline_prompt
    spec = ...  # WikiPageSpec
    prompt = _build_outline_prompt(
        spec,
        ...,
        sibling_titles=["质量校验与修订", "Mermaid 图表优化"],
        out_of_scope_topics=[
            "Validates outline JSON",
            "Sanitizes Mermaid diagrams",
        ],
    )
    text = prompt.text if hasattr(prompt, "text") else "".join(p.text for p in prompt)
    assert "Sibling pages" in text
    assert "质量校验与修订" in text
    assert "DO NOT cover" in text
    assert "Out-of-scope" in text
    assert "Validates outline JSON" in text
```

Run: FAIL.

- [ ] **Step 2: Extend `generate_page_outline` signature**

```python
async def generate_page_outline(
    spec: WikiPageSpec,
    ...,
    sibling_titles: list[str] | None = None,
    out_of_scope_topics: list[str] | None = None,
) -> PageOutline:
    ...
```

In `_build_outline_prompt`, inject into the cacheable prefix:

```python
if sibling_titles:
    siblings_block = (
        "Sibling pages (DO NOT cover their topics; reference by name only):\n"
        + "\n".join(f"- {t}" for t in sibling_titles)
    )
if out_of_scope_topics:
    oos_block = "Out-of-scope (covered elsewhere):\n" + "\n".join(
        f"- {t}" for t in out_of_scope_topics
    )
```

- [ ] **Step 3: Wire up in `page_generator.generate_page_batch`**

In `worker/pipeline/page_generator.py:266-274`, before calling `generate_page_outline`:

```python
siblings = [
    p.title for p in plan.pages
    if p.parent == spec.parent and p.title != spec.title
]
out_of_scope = [
    _first_sentence(p.purpose) for p in plan.pages
    if p.parent == spec.parent and p.title != spec.title and p.purpose
]
```

`_first_sentence` is a small local helper that returns the substring up to the first `.` or full-width `。`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/worker/test_page_outline.py tests/worker/test_page_generator.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/page_outline.py worker/pipeline/page_generator.py tests/worker/
git commit -m "feat(page): inject sibling titles + out-of-scope topics into outline prompt (A2)"
```

---

### Task A.3: Scope discipline rule in draft prompt (A3)

**Files:**
- Modify: `worker/pipeline/page_draft.py:27` — `DRAFT_SYSTEM`.
- Test: `tests/worker/test_page_draft.py`

- [ ] **Step 1: Write the failing test**

```python
def test_draft_system_prompt_includes_scope_discipline():
    from worker.pipeline.page_draft import DRAFT_SYSTEM

    text = "".join(seg.text for seg in DRAFT_SYSTEM) if isinstance(DRAFT_SYSTEM, list) else DRAFT_SYSTEM
    assert "scope" in text.lower()
    assert "sibling" in text.lower()
    assert "≤ 1 sentence" in text or "one sentence" in text.lower()
```

- [ ] **Step 2: Append the rule**

To `DRAFT_SYSTEM`, append:

> **Scope discipline.** Stay strictly within the assigned files for this page. If a topic is owned by a sibling page listed in the prompt, give it ≤ 1 sentence and refer to the sibling by title. Never re-document a sibling's primary subject in this page.

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/worker/test_page_draft.py -v
git add worker/pipeline/page_draft.py tests/worker/test_page_draft.py
git commit -m "feat(page): scope discipline rule in draft system prompt (A3)"
```

---

### Task A.4: Multi-query retrieval + rank-weighted chunk quota (A4)

**Files:**
- Modify: `worker/pipeline/page_generator.py:224-249` — replace single query with five; add `_balance_chunks`.
- Test: `tests/worker/test_page_generator.py`

- [ ] **Step 1: Write the failing test**

```python
def test_balance_chunks_rank_weighted_quota():
    from worker.pipeline.page_generator import _balance_chunks

    files = ["a.py", "b.py", "c.py", "d.py", "e.py"]
    chunks = [_chunk(f) for f in files for _ in range(20)]  # 100 chunks total
    out = _balance_chunks(chunks, files=files, k=30, floor=2)

    counts = {f: sum(1 for c in out if c.file == f) for f in files}
    assert counts["a.py"] >= counts["b.py"] >= counts["c.py"] >= counts["d.py"] >= counts["e.py"]
    assert all(counts[f] >= 2 for f in files)  # floor
    assert sum(counts.values()) == 30


def test_balance_chunks_floor_only_for_low_rank():
    """Rank >= 6 receives only the floor."""
    from worker.pipeline.page_generator import _balance_chunks
    files = [f"f{i}.py" for i in range(8)]
    chunks = [_chunk(f) for f in files for _ in range(20)]
    out = _balance_chunks(chunks, files=files, k=30, floor=2)
    counts = {f: sum(1 for c in out if c.file == f) for f in files}
    for i in range(6, 8):
        assert counts[f"f{i}.py"] == 2  # floor only
```

Run: FAIL.

- [ ] **Step 2: Implement `_balance_chunks`**

```python
def _balance_chunks(
    chunks: list[Chunk],
    *,
    files: list[str],
    k: int,
    floor: int = 2,
) -> list[Chunk]:
    """Rank-weighted graduated quota.

    quota_i = max(floor, round(k * w_i / sum(w))) where w_i = 1 / (rank_i + 1).
    Files at rank >= 6 receive only the floor.
    """
    if not files:
        return chunks[:k]

    weights = [1.0 / (i + 1) if i < 6 else 0.0 for i in range(len(files))]
    total_w = sum(weights)
    quotas = [
        max(floor, round(k * w / total_w)) if w > 0 else floor
        for w in weights
    ]
    # adjust for rounding so sum == k
    diff = k - sum(quotas)
    if diff > 0:
        quotas[0] += diff
    elif diff < 0:
        for i in range(len(quotas) - 1, -1, -1):
            take = min(-diff, quotas[i] - floor)
            quotas[i] -= take
            diff += take
            if diff == 0:
                break

    by_file: dict[str, list[Chunk]] = {f: [] for f in files}
    leftovers: list[Chunk] = []
    for c in chunks:
        if c.file in by_file:
            by_file[c.file].append(c)
        else:
            leftovers.append(c)

    out: list[Chunk] = []
    for f, q in zip(files, quotas):
        out.extend(by_file[f][:q])
    # fill any unmet quota from leftovers (other files in the index)
    while len(out) < k and leftovers:
        out.append(leftovers.pop(0))
    return out[:k]
```

- [ ] **Step 3: Replace the single-query block at line 224-249**

```python
queries = [
    spec.title,
    spec.purpose or "",
    " ".join(spec.en_keywords or []),
    " ".join(top5_entity_names),
    " ".join(_file_stems(spec.files)),
]
queries = [q for q in queries if q.strip()]

raw_chunks = store.multi_search(
    [embedding.embed(q) for q in queries],
    k=top_k * 2,
    doc_k=1,
)
context_chunks = _balance_chunks(
    raw_chunks,
    files=spec.files,
    k=top_k,
    floor=2,
)

log_validation_retry(  # use the structured event channel
    "page_generator.balance_chunks",
    {
        "page": spec.title,
        "files": spec.files,
        "allocated_per_file": {f: sum(1 for c in context_chunks if c.file == f) for f in spec.files},
        "leftover": top_k - sum(min(...)),  # diagnostic
    },
)
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/worker/test_page_generator.py -v
git add worker/pipeline/page_generator.py tests/worker/test_page_generator.py
git commit -m "feat(page): multi-query retrieval with rank-weighted chunk quota (A4)"
```

---

### Task A.5: Rank-weighted entity quota (A5)

**Files:**
- Modify: `worker/pipeline/page_formatters.py:41` — `_format_entity_details` accepts `files: list[str]`.
- Test: `tests/worker/test_page_generator.py`

- [ ] **Step 1: Write the failing test**

```python
def test_format_entity_details_rank_weighted_per_file_quota():
    from worker.pipeline.page_formatters import _format_entity_details

    files = ["a.py", "b.py", "c.py"]
    entities = [_entity(file=f, name=f"e{i}") for f in files for i in range(20)]
    out = _format_entity_details(entities, max_count=15, files=files)

    by_file = {f: out.count(f) for f in files}
    assert by_file["a.py"] > by_file["b.py"] > by_file["c.py"]
    assert all(by_file[f] >= 1 for f in files)  # floor
```

- [ ] **Step 2: Refactor `_format_entity_details`**

Reuse the same allocation logic as `_balance_chunks` (extract to a private `_rank_weighted_quotas(files, k, floor)` helper in `worker/pipeline/page_formatters.py` or import from `page_generator`).

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/worker/test_page_generator.py -v
git add worker/pipeline/page_formatters.py tests/worker/
git commit -m "feat(page): rank-weighted entity quota (A5)"
```

---

### Task A.6: Signature slices in outline prompt (A6)

**Files:**
- Modify: `worker/pipeline/page_outline.py:_build_outline_prompt` — accept `signature_slices: dict[file, list[str]]`.
- Modify: `worker/pipeline/page_generator.py` — derive slices via `extract_source_slice` (from `code_slices.py` per A13).
- Test: `tests/worker/test_page_outline.py`

- [ ] **Step 1: Write the failing test**

```python
def test_outline_prompt_includes_signature_slices():
    from worker.pipeline.page_outline import _build_outline_prompt

    prompt = _build_outline_prompt(
        spec=...,
        ...,
        signature_slices={
            "page_generator.py": [
                "def generate_page(spec, plan, ...):\n    \"\"\"Run 4-pass generation for one page.\"\"\"\n    ...",
            ],
        },
    )
    text = "".join(seg.text for seg in prompt) if isinstance(prompt, list) else prompt
    assert "Signature slices" in text
    assert "page_generator.py" in text
    assert "def generate_page" in text
```

- [ ] **Step 2: Implement signature slice block**

In `_build_outline_prompt`:

```python
if signature_slices:
    blocks = []
    for path, slices in signature_slices.items():
        for s in slices:
            blocks.append(f"### {path}\n```\n{s}\n```")
    block_text = "Signature slices:\n" + "\n".join(blocks)
```

In the page generator, derive slices for each `spec.file`:

```python
from worker.pipeline.retrieval.code_slices import extract_source_slice

signature_slices = {}
for path in spec.files[:3]:  # only top-3 to bound prompt
    fa = file_analysis_by_path.get(path)
    if not fa:
        continue
    top_entities = sorted(
        fa.entities, key=lambda e: e.importance, reverse=True
    )[:2]
    slices = []
    for ent in top_entities:
        text = extract_source_slice(
            clone_root / path, ent.start_line, ent.start_line + 4
        )
        if text:
            slices.append(text)
    if slices:
        signature_slices[path] = slices
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/worker/test_page_outline.py tests/worker/test_page_generator.py -v
git add worker/pipeline/ tests/worker/
git commit -m "feat(page): inject signature slices into outline prompt (A6)"
```

---

### Task A.15: Sub-package restructure (A15)

**Files:**
- All files listed in the "Renamed/moved files" section above.
- Modify: `worker/pipeline/__init__.py` — back-compat re-exports.
- Create: deprecation shim at every old path.
- Modify: every test import that touches a moved module.
- Test: `tests/worker/test_pipeline_layout.py`

> **Why last:** A1–A14 modify files at their old paths. A15 moves them in bulk; doing it last avoids per-task merge churn. After this task every later phase (B1, B2) lands in the new layout natively.

- [ ] **Step 1: Write the failing test**

Create `tests/worker/test_pipeline_layout.py`:

```python
import importlib
import warnings
from pathlib import Path

import pytest


def test_pipeline_top_level_is_only_stages_and_helpers():
    pipeline_dir = Path("worker/pipeline")
    files = sorted(p.name for p in pipeline_dir.iterdir() if p.is_file())
    expected = {
        "__init__.py",
        "language.py",
        "pipeline_logging.py",
        "ingestion.py",
        "ast_analysis.py",
        "dependency_graph.py",
    }
    # plus deprecation shims at old paths (one-release window)
    deprecation_shims = {
        "page_generator.py", "page_outline.py", "page_draft.py",
        "page_formatters.py", "fact_check.py", "diagram_post_processor.py",
        "wiki_planner.py", "outline_anchors.py", "user_steering.py",
        "fast_report_index.py", "rag_indexer.py",
    }
    actual = set(files)
    extra = actual - expected - deprecation_shims
    assert not extra, f"unexpected top-level files: {extra}"


def test_back_compat_reexports_resolve():
    from worker.pipeline import (
        WikiPlanner,
        WikiPageSpec,
        WikiPlan,
        generate_page,
        generate_page_batch,
        compute_generation_order,
        PageResult,
        validate_outline,
        PageOutline,
        SectionPlan,
        DiagramPlan,
        FileAnalysis,
        DependencyGraph,
    )
    # symbols load
    assert WikiPlanner is not None


@pytest.mark.parametrize("old_module", [
    "worker.pipeline.page_generator",
    "worker.pipeline.page_outline",
    "worker.pipeline.wiki_planner",
])
def test_old_paths_emit_deprecation_warning(old_module):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module(old_module)
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
```

Run: FAIL.

- [ ] **Step 2: Move files in bulk**

```bash
mkdir -p worker/pipeline/page worker/pipeline/planner
touch worker/pipeline/page/__init__.py worker/pipeline/planner/__init__.py

git mv worker/pipeline/page_generator.py worker/pipeline/page/generator.py
git mv worker/pipeline/page_outline.py worker/pipeline/page/outline.py
git mv worker/pipeline/page_draft.py worker/pipeline/page/draft.py
git mv worker/pipeline/page_formatters.py worker/pipeline/page/formatters.py
git mv worker/pipeline/fact_check.py worker/pipeline/page/fact_check.py
git mv worker/pipeline/diagram_post_processor.py worker/pipeline/page/diagram_post_processor.py
git mv worker/pipeline/wiki_planner.py worker/pipeline/planner/wiki_planner.py
git mv worker/pipeline/outline_anchors.py worker/pipeline/planner/outline_anchors.py
git mv worker/pipeline/user_steering.py worker/pipeline/planner/user_steering.py
```

- [ ] **Step 3: Update internal imports**

```bash
# Update intra-package imports across worker/pipeline, worker/index, worker/jobs.py, api/, cli/, tests/
# Strategy: ripgrep + manual edit per file. Do NOT use blanket sed — many imports are aliased.
grep -rln "from worker.pipeline.page_generator\|from worker.pipeline.page_outline\|from worker.pipeline.page_draft\|from worker.pipeline.page_formatters\|from worker.pipeline.fact_check\|from worker.pipeline.diagram_post_processor\|from worker.pipeline.wiki_planner\|from worker.pipeline.outline_anchors\|from worker.pipeline.user_steering" worker/ api/ cli/ tests/
```

For each file in the list, rewrite imports to the new path (e.g. `from worker.pipeline.page.generator import generate_page`).

- [ ] **Step 4: Add deprecation shims at old paths**

Create one shim per moved module. Pattern:

```python
# worker/pipeline/page_generator.py (shim)
"""Deprecated: import from ``worker.pipeline.page.generator`` instead."""

from __future__ import annotations

import warnings

warnings.warn(
    "worker.pipeline.page_generator is deprecated; import from "
    "worker.pipeline.page.generator instead",
    DeprecationWarning,
    stacklevel=2,
)

from worker.pipeline.page.generator import *  # noqa: F401, F403, E402
```

- [ ] **Step 5: Re-export top-level symbols from `worker/pipeline/__init__.py`**

```python
from worker.pipeline.page.generator import (
    generate_page,
    generate_page_batch,
    compute_generation_order,
    PageResult,
)
from worker.pipeline.page.outline import (
    PageOutline,
    SectionPlan,
    DiagramPlan,
    validate_outline,
)
from worker.pipeline.planner.wiki_planner import (
    WikiPlanner,
    WikiPageSpec,
    WikiPlan,
)
from worker.pipeline.ast_analysis import FileAnalysis
from worker.pipeline.dependency_graph import DependencyGraph
```

- [ ] **Step 6: Run all tests**

```bash
uv run pytest tests/ --ignore=tests/e2e -q
```

Expected: same baseline pass count as Task A.0 step 2, plus the new layout tests passing.

- [ ] **Step 7: Lint + format**

```bash
uv run ruff check . && uv run ruff format --check .
```

Fix any issues (mostly import ordering after the move).

- [ ] **Step 8: Commit**

```bash
git add worker/ api/ cli/ tests/
git commit -m "refactor(pipeline): split into retrieval/, planner/, page/ sub-packages with deprecation shims (A15)"
```

---

### Task A.End: Self-index regression + Phase A acceptance

- [ ] **Step 1: Run self-index regression**

```bash
cd /Users/lazyxiang/code/AutoWiki
uv run autowiki index github.com/lazyxiang/AutoWiki --reuse-index=false
```

Open the resulting `wiki_plan.json`. Verify the eight pages from spec §5.2 carry the expected primary file as `files[0]`:

| Page | Expected `files[0]` |
|---|---|
| 依赖图谱构建 | `dependency_graph.py` |
| 内容生成引擎 | `page_generator.py` (or `page/generator.py`) |
| 质量校验与修订 | `fact_check.py` |
| Mermaid 图表优化 | `mermaid.py` |
| 前端应用架构 | a `web/` file |
| Wiki 渲染组件 | a `web/components/` file |
| 后端接口与服务 | an `api/` file |
| 实时通信机制 | a WebSocket-related file |

- [ ] **Step 2: Confirm Phase A acceptance criteria from spec §7**

Verify items 1–6 from §7. Document any miss in the PR description with mitigation plan.

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/wiki-quality-layer-a
gh pr create --title "feat(wiki): planner & prompt patches for page quality (Layer A)" --body "..."
```

PR body references spec sections 1–6 and lists each A1–A15 patch with its commit.

---

## Phase B1 — Deterministic Keyword Index (PR `feat/keyword-index`)

> **Gate:** Phase A merged and self-index acceptance criteria green.

### Task B1.0: Worktree + dependency

**Files:**
- Modify: `pyproject.toml` — add `rank-bm25>=0.2.2`.

- [ ] **Step 1: Worktree**

```bash
cd /Users/lazyxiang/code/AutoWiki
git fetch origin main
git worktree add -b feat/keyword-index ~/code/AutoWiki-worktrees/keyword-index origin/main
cd ~/code/AutoWiki-worktrees/keyword-index
```

- [ ] **Step 2: Add `rank-bm25`**

In `pyproject.toml`:

```toml
dependencies = [
    ...,
    "rank-bm25>=0.2.2",
]
```

```bash
uv sync
```

- [ ] **Step 3: Smoke test the import**

```bash
uv run python -c "from rank_bm25 import BM25Okapi; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add rank-bm25 for BM25 keyword retrieval"
```

---

### Task B1.1: `KeywordIndex` skeleton (B1)

**Files:**
- Create: `worker/pipeline/retrieval/keyword_index.py`
- Test: `tests/worker/test_keyword_index.py`

- [ ] **Step 1: Write the failing test**

```python
from worker.pipeline.retrieval.keyword_index import KeywordIndex
from worker.pipeline.rag_indexer import Chunk  # still present in B1


def test_search_returns_top_k_for_single_query():
    chunks = [
        Chunk(file="a.py", text="dependency graph build_graph", line_start=1, line_end=2),
        Chunk(file="b.py", text="wiki planner phase 2", line_start=1, line_end=2),
        Chunk(file="c.py", text="fact check verdict", line_start=1, line_end=2),
    ]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["dependency graph"], k=1)
    assert out[0].file == "a.py"


def test_search_applies_per_file_quota():
    chunks = [Chunk(file="a.py", text="x"*20, line_start=i, line_end=i) for i in range(10)]
    chunks += [Chunk(file="b.py", text="x"*20, line_start=i, line_end=i) for i in range(10)]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["x"], k=10, files=["a.py", "b.py"], per_file_quota=2)
    counts = {"a.py": 0, "b.py": 0}
    for c in out:
        counts[c.file] += 1
    assert counts["a.py"] == 2
    assert counts["b.py"] == 2


def test_search_files_filter_restricts_scope():
    chunks = [
        Chunk(file="a.py", text="alpha", line_start=1, line_end=1),
        Chunk(file="b.py", text="alpha", line_start=1, line_end=1),
    ]
    idx = KeywordIndex.build(chunks, repo_index={"files": []})
    out = idx.search(["alpha"], k=5, files=["a.py"])
    assert all(c.file == "a.py" for c in out)
```

Run: FAIL.

- [ ] **Step 2: Implement `KeywordIndex`**

```python
"""Deterministic BM25 keyword index for wiki page retrieval.

Replaces ``FAISSStore.multi_search`` for the wiki path. Pure-Python via
``rank_bm25``; consumes the shared tokenizer from ``worker.utils.tokenize``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from worker.pipeline.rag_indexer import Chunk
from worker.utils.tokenize import tokenize_text


@dataclass
class KeywordIndex:
    chunks: list[Chunk]
    bm25: BM25Okapi
    file_to_chunks: dict[str, list[int]]
    token_idf: dict[str, float] = field(default_factory=dict)

    @classmethod
    def build(cls, chunks: list[Chunk], *, repo_index: dict) -> "KeywordIndex":
        tokenized = [list(tokenize_text(c.text)) for c in chunks]
        bm25 = BM25Okapi(tokenized)
        file_to_chunks: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(chunks):
            file_to_chunks[c.file].append(i)
        token_idf = dict(
            zip(bm25.idf.keys(), bm25.idf.values())
        ) if hasattr(bm25, "idf") else {}
        return cls(
            chunks=chunks,
            bm25=bm25,
            file_to_chunks=dict(file_to_chunks),
            token_idf=token_idf,
        )

    def search(
        self,
        queries: list[str],
        *,
        k: int,
        files: list[str] | None = None,
        per_file_quota: int = 2,
    ) -> list[Chunk]:
        # union top-k across queries with score-summing for shared chunks
        scores: dict[int, float] = defaultdict(float)
        for q in queries:
            tokens = list(tokenize_text(q))
            if not tokens:
                continue
            for i, s in enumerate(self.bm25.get_scores(tokens)):
                if files and self.chunks[i].file not in files:
                    continue
                scores[i] += s

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

        if files and per_file_quota > 0:
            quotas = {f: per_file_quota for f in files}
            picked: list[int] = []
            leftovers: list[int] = []
            for idx, _ in ranked:
                f = self.chunks[idx].file
                if quotas.get(f, 0) > 0:
                    picked.append(idx)
                    quotas[f] -= 1
                else:
                    leftovers.append(idx)
                if len(picked) >= k:
                    break
            picked.extend(leftovers[: max(0, k - len(picked))])
            return [self.chunks[i] for i in picked[:k]]

        return [self.chunks[i] for i, _ in ranked[:k]]
```

- [ ] **Step 3: Run tests + commit**

```bash
uv run pytest tests/worker/test_keyword_index.py -v
git add worker/pipeline/retrieval/keyword_index.py tests/worker/test_keyword_index.py
git commit -m "feat(retrieval): KeywordIndex BM25 keyword retrieval (B1)"
```

---

### Task B1.2: Recall-parity gate vs FAISS

**Files:**
- Test: `tests/worker/test_keyword_index.py` (extend with parity test)

> **Per spec §6.1 and §6.4:** B1 ships only if recall parity vs FAISS is within ±10% on a 100-question fixture. This is the gate before B2 deletes FAISS.

- [ ] **Step 1: Build the fixture**

Create `tests/fixtures/keyword_index_recall/questions.json` — 100 Q/A pairs against `tests/fixtures/simple-repo/`. Each entry:

```json
{
  "query": "How does the dependency graph get built?",
  "expected_files": ["dependency_graph.py"]
}
```

(Use the actual paths in `tests/fixtures/simple-repo/`. Generate the fixture by running the existing FAISS retriever on a hand-written question list and recording the top-5 file set.)

- [ ] **Step 2: Write the parity test**

```python
def test_keyword_index_recall_parity_vs_faiss():
    """BM25 top-5 recall is within ±10% of FAISS top-5 on the fixture."""
    questions = json.load(open("tests/fixtures/keyword_index_recall/questions.json"))
    repo_dir = Path("tests/fixtures/simple-repo")

    chunks = _build_chunks(repo_dir)  # existing test helper
    faiss_store = _build_faiss(chunks)  # via mock_embedding fixture
    keyword = KeywordIndex.build(chunks, repo_index={})

    faiss_hits = sum(
        any(c.file in q["expected_files"] for c in faiss_store.search(q["query"], k=5))
        for q in questions
    )
    bm25_hits = sum(
        any(c.file in q["expected_files"] for c in keyword.search([q["query"]], k=5))
        for q in questions
    )

    faiss_recall = faiss_hits / len(questions)
    bm25_recall = bm25_hits / len(questions)
    assert bm25_recall >= faiss_recall - 0.10, (
        f"BM25 recall {bm25_recall:.2f} below FAISS {faiss_recall:.2f} - 0.10"
    )
```

Run:

```bash
uv run pytest tests/worker/test_keyword_index.py::test_keyword_index_recall_parity_vs_faiss -v
```

Expected: PASS. If FAIL, iterate on the tokenizer or BM25 parameters before proceeding.

- [ ] **Step 3: Commit + push + PR**

```bash
git add tests/fixtures/keyword_index_recall/ tests/worker/test_keyword_index.py
git commit -m "test(retrieval): recall-parity gate for KeywordIndex vs FAISS (B1)"
git push -u origin feat/keyword-index
gh pr create --title "feat(retrieval): KeywordIndex (BM25) with recall-parity gate (B1)" --body "..."
```

---

## Phase B2 — Section drafting + Stage 4 deletion (PR `feat/section-drafting-and-stage4-removal`)

> **Gate:** Phase B1 merged with parity gate green.

### Task B2.0: Worktree

```bash
cd /Users/lazyxiang/code/AutoWiki
git fetch origin main
git worktree add -b feat/section-drafting-and-stage4-removal ~/code/AutoWiki-worktrees/section-drafting origin/main
cd ~/code/AutoWiki-worktrees/section-drafting
uv sync
```

---

### Task B2.1: Add `out_of_scope_claims` to outline schema (B3)

**Files:**
- Modify: `worker/pipeline/page/outline.py:_OUTLINE_SCHEMA` — add `out_of_scope_claims: array<string>`.
- Modify: `worker/pipeline/page/fact_check.py` — receive `out_of_scope_claims`; fail verdict if a draft contains a matching claim.
- Modify: `worker/pipeline/page/generator.py` — Pass 4 strips offending sentences pre-revision.
- Test: `tests/worker/test_page_outline.py`, `tests/worker/test_fact_check.py`.

- [ ] **Step 1: Test — schema**

```python
def test_outline_schema_includes_out_of_scope_claims():
    from worker.pipeline.page.outline import _OUTLINE_SCHEMA
    assert "out_of_scope_claims" in _OUTLINE_SCHEMA["properties"]
    field = _OUTLINE_SCHEMA["properties"]["out_of_scope_claims"]
    assert field["type"] == "array"
    assert field["items"]["type"] == "string"
```

- [ ] **Step 2: Test — fact-check fails on out-of-scope claim**

```python
def test_factcheck_fails_when_draft_contains_out_of_scope_claim():
    from worker.pipeline.page.fact_check import run_fact_check

    draft = "This module validates outline JSON before drafting."
    outline = PageOutline(
        out_of_scope_claims=["Validates outline JSON"],
        ...,
    )
    result = run_fact_check(draft=draft, outline=outline, ...)
    assert result.verdict == "fail"
    assert any("Validates outline JSON" in i.detail for i in result.issues)
```

Run: FAIL.

- [ ] **Step 3: Implement**

Schema:

```python
_OUTLINE_SCHEMA["properties"]["out_of_scope_claims"] = {
    "type": "array",
    "items": {"type": "string"},
    "default": [],
}
```

In `fact_check.run_fact_check`, before sending to the LLM, scan `draft` for substring matches against `outline.out_of_scope_claims` (case-insensitive, normalized whitespace). If any hit, return early with `verdict="fail"`.

Pass 4 (`page/generator.py`): if fact-check returned out-of-scope hits, strip the matched sentences before invoking the revision LLM call.

- [ ] **Step 4: Run + commit**

```bash
uv run pytest tests/worker/test_page_outline.py tests/worker/test_fact_check.py -v
git add worker/pipeline/page/ tests/worker/
git commit -m "feat(page): out_of_scope_claims gate fact-check (B3)"
```

---

### Task B2.2: Section drafter — Pass 2a Skeleton (B2)

**Files:**
- Create: `worker/pipeline/page/section_drafter.py` — Skeleton phase only, in this task.
- Test: `tests/worker/test_section_drafter.py`

- [ ] **Step 1: Test**

```python
def test_skeleton_returns_markdown_with_h1_and_section_headings():
    from worker.pipeline.page.section_drafter import build_skeleton

    outline = PageOutline(
        title="Wiki Planner",
        sections=[
            SectionPlan(heading="Phase 1: Outline", focus="...", source_files=["wiki_planner.py"]),
            SectionPlan(heading="Phase 2: File Selection", focus="...", source_files=["wiki_planner.py"]),
        ],
        ...,
    )
    md = await build_skeleton(outline=outline, fast_llm=mock_fast_llm)
    assert md.startswith("# Wiki Planner")
    assert "## Phase 1: Outline" in md
    assert "## Phase 2: File Selection" in md
```

- [ ] **Step 2: Implement `build_skeleton`**

Calls `fast_llm` with a prompt that asks for an H1 + ordered H2 sections + one-line purpose under each heading. Output is Markdown text (~30 lines). Per spec §5.3 B2:

```python
SKELETON_SYSTEM = [
    PromptSegment(
        text=(
            "You are drafting the rendered shape of a wiki page from its outline. "
            "Output Markdown only: a single H1 with the page title, then H2 headings for each section in order, "
            "with a one-sentence purpose line under each heading. Do not write body text."
        )
    )
]


async def build_skeleton(*, outline: PageOutline, fast_llm: LLMProvider) -> str:
    user = f"# {outline.title}\n\nSections:\n" + "\n".join(
        f"- {s.heading}: {s.focus}" for s in outline.sections
    )
    return await fast_llm.generate([
        *SKELETON_SYSTEM,
        PromptSegment(text=user),
    ])
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/worker/test_section_drafter.py -v
git add worker/pipeline/page/section_drafter.py tests/worker/
git commit -m "feat(page): section drafter skeleton phase (B2 part 1)"
```

---

### Task B2.3: Section drafter — Pass 2b per-section (B2)

**Files:**
- Modify: `worker/pipeline/page/section_drafter.py` — add `draft_section`.

- [ ] **Step 1: Test**

```python
def test_draft_section_uses_keyword_index_with_section_scope():
    from worker.pipeline.page.section_drafter import draft_section

    section = SectionPlan(
        heading="Phase 2: File Selection",
        focus="How files are scored",
        source_files=["wiki_planner.py"],  # restricted scope
        diagram=None,
    )
    captured_files = {}

    class StubKeywordIndex:
        def search(self, queries, *, k, files=None, per_file_quota=2):
            captured_files["files"] = files
            return []

    await draft_section(
        section=section,
        spec_files=["wiki_planner.py", "page_generator.py"],
        keyword_index=StubKeywordIndex(),
        llm=mock_llm,
    )
    # scope = spec.files ∩ section.source_files
    assert set(captured_files["files"]) == {"wiki_planner.py"}
```

- [ ] **Step 2: Implement `draft_section`**

```python
async def draft_section(
    *,
    section: SectionPlan,
    spec_files: list[str],
    keyword_index: KeywordIndex,
    llm: LLMProvider,
    skeleton_heading: str,
) -> str:
    queries = [section.heading, section.focus or ""]
    queries += [e.name for e in (section.entities or [])[:5]]
    section_scope = (
        list(set(spec_files) & set(section.source_files or [])) or spec_files
    )
    chunks = keyword_index.search(
        queries=[q for q in queries if q.strip()],
        k=8,
        files=section_scope,
        per_file_quota=2,
    )
    prompt = _build_section_prompt(
        section=section,
        chunks=chunks,
        skeleton_heading=skeleton_heading,
    )
    return await llm.generate(prompt)
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/worker/test_section_drafter.py -v
git add worker/pipeline/page/section_drafter.py tests/worker/
git commit -m "feat(page): section drafter per-section phase (B2 part 2)"
```

---

### Task B2.4: Section drafter — Pass 2c Stitch + orchestrator (B2)

**Files:**
- Modify: `worker/pipeline/page/section_drafter.py` — add `stitch_sections` + top-level `draft_page_in_sections`.
- Modify: `worker/pipeline/page/generator.py` — replace the single `page_draft.draft` call with `draft_page_in_sections`.

- [ ] **Step 1: Test**

```python
def test_draft_page_in_sections_returns_full_markdown():
    md = await draft_page_in_sections(
        spec=...,
        outline=...,
        keyword_index=...,
        llm=mock_llm,
        fast_llm=mock_fast_llm,
    )
    assert md.startswith("# ")
    # section-by-section content present
    assert "## Phase 1" in md and "## Phase 2" in md


def test_draft_page_in_sections_fails_loudly_on_retry_exhaustion():
    """No legacy_full_draft fallback per spec §5.3 B2."""
    from worker.pipeline.page.section_drafter import WikiGenerationError

    failing_llm = _llm_that_always_raises()
    with pytest.raises(WikiGenerationError):
        await draft_page_in_sections(
            spec=...,
            outline=...,
            keyword_index=...,
            llm=failing_llm,
            fast_llm=mock_fast_llm,
        )
```

- [ ] **Step 2: Implement `stitch_sections` + orchestrator**

```python
async def stitch_sections(
    *,
    skeleton: str,
    sections: list[tuple[str, str]],  # (heading, body)
    fast_llm: LLMProvider,
) -> str:
    """Reconcile transitions between section bodies; output final Markdown."""
    user = "Skeleton:\n" + skeleton + "\n\nSection drafts:\n" + "\n\n".join(
        f"## {h}\n{b}" for h, b in sections
    )
    return await fast_llm.generate([
        PromptSegment(text=STITCH_SYSTEM),
        PromptSegment(text=user),
    ])


async def draft_page_in_sections(
    *,
    spec: WikiPageSpec,
    outline: PageOutline,
    keyword_index: KeywordIndex,
    llm: LLMProvider,
    fast_llm: LLMProvider,
) -> str:
    skeleton = await build_skeleton(outline=outline, fast_llm=fast_llm)
    drafts: list[tuple[str, str]] = []
    for section in outline.sections:
        body = await draft_section(
            section=section,
            spec_files=spec.files,
            keyword_index=keyword_index,
            llm=llm,
            skeleton_heading=section.heading,
        )
        drafts.append((section.heading, body))
    return await stitch_sections(
        skeleton=skeleton,
        sections=drafts,
        fast_llm=fast_llm,
    )
```

`WikiGenerationError` is raised inside `llm.generate` after `max_retries` exhausted (existing behavior in `pipeline_logging`). No fallback path is added.

- [ ] **Step 3: Wire up in `page/generator.py`**

Replace the call to `page_draft.draft` in `generate_page` with `draft_page_in_sections`. Drop the `embedding` and `store` parameters from the call signature in this task; the next task removes them entirely.

- [ ] **Step 4: Run + commit**

```bash
uv run pytest tests/worker/test_section_drafter.py tests/worker/test_page_generator.py -v
git add worker/pipeline/page/ tests/worker/
git commit -m "feat(page): section-level drafting replaces single-shot draft (B2 part 3)"
```

---

### Task B2.5: Delete Stage 4 + `rag_indexer.py` + `EmbeddingProvider` (B4)

**Files:**
- Delete: `worker/pipeline/rag_indexer.py`
- Modify: `worker/index/full.py:17,321,...` — remove `make_embedding_provider`, remove the `Stage 4` call.
- Modify: `worker/index/refresh.py:18,481,...` — same.
- Modify: `worker/index/artifacts.py` — remove FAISS index lookup/loader.
- Modify: `worker/pipeline/page/generator.py:30,36,192,195,373,376` — drop `EmbeddingProvider` and `FAISSStore` parameters.
- Modify: `worker/jobs.py` — drop embedding from every entrypoint.
- Modify: `shared/config.py` (if applicable) — mark embedding fields deprecated, log warning when set.
- Test: `tests/worker/test_rag_indexer.py` → delete.
- Test: `tests/worker/test_index_artifacts.py` — extend with no-FAISS-files assertion.

- [ ] **Step 1: Audit remaining FAISS imports**

```bash
grep -rn "FAISSStore\|EmbeddingProvider\|make_embedding_provider\|rag_indexer" worker/ api/ cli/ tests/ shared/
```

Expected after this task: hits only inside `worker/embedding/` (kept on disk per spec) and `worker/research/` (disabled in B5).

- [ ] **Step 2: Write the failing test**

Extend `tests/worker/test_index_artifacts.py`:

```python
def test_fresh_index_does_not_produce_faiss_files(simple_repo_indexed: Path):
    repo_dir = simple_repo_indexed
    assert not (repo_dir / "faiss.index").exists()
    assert not (repo_dir / "faiss.meta.pkl").exists()


def test_indexing_runs_without_embedding_provider(monkeypatch, simple_repo_clone):
    """Stage 1-5 complete with no embedding API key set anywhere."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    # ... and config has no embedding section
    cfg = Config(...)  # explicitly no embedding fields
    asyncio.run(run_full_index(cfg=cfg, repo_url="...", ...))
```

Run: FAIL initially.

- [ ] **Step 3: Delete Stage 4**

In `worker/index/full.py`, find the block that calls `build_chunks` + `FAISSStore.build` (Stage 4). Delete it entirely. Remove the `make_embedding_provider` import and its call site (line 321).

Same in `worker/index/refresh.py` (line 481).

- [ ] **Step 4: Delete `rag_indexer.py`**

```bash
git rm worker/pipeline/rag_indexer.py tests/worker/test_rag_indexer.py
```

If any module still imports `Chunk` from `rag_indexer`, move the `Chunk` dataclass to `worker/pipeline/retrieval/chunk.py` (a tiny file) and update imports. `KeywordIndex` consumes `Chunk`, so this must work.

```bash
mkdir -p worker/pipeline/retrieval
# edit worker/pipeline/retrieval/chunk.py with the Chunk dataclass
```

Update `worker/pipeline/retrieval/keyword_index.py` import:

```python
from worker.pipeline.retrieval.chunk import Chunk
```

- [ ] **Step 5: Drop `EmbeddingProvider` parameter from page generator**

In `worker/pipeline/page/generator.py`, remove:
- import of `EmbeddingProvider` (line 30)
- import of `FAISSStore` (line 36)
- `embedding`, `store` parameters in `generate_page` (line 192-195) and `generate_page_batch` (line 373-376).

Replace `store.multi_search(...)` calls inside `draft_section` (already added in B2.3) — `KeywordIndex` is constructed in `generate_page_batch` from `chunks` produced by a fresh, in-memory chunk pass over the clone (no FAISS persistence).

- [ ] **Step 6: Update `worker/jobs.py` entrypoints**

Drop `embedding` and `store` params from every job function in `worker/jobs.py` and the orchestrators in `worker/index/full.py` / `worker/index/refresh.py` / the wiki pipeline. Each entrypoint now constructs `KeywordIndex` from chunks produced inline.

- [ ] **Step 7: Deprecate config fields**

In `shared/config.py` (or wherever `LLMConfig.embedding` lives), keep the field but log a deprecation warning when it's set:

```python
if cfg.llm.embedding_provider or cfg.embedding:
    log.warning(
        "embedding configuration is deprecated and ignored; will be removed in a future release"
    )
```

- [ ] **Step 8: Run all tests**

```bash
uv run pytest tests/ --ignore=tests/e2e -q
```

Expected: PASS. `test_research_*` tests will fail until Task B2.6 — handle that next.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(index): delete Stage 4, rag_indexer, and EmbeddingProvider parameter; rely on KeywordIndex (B4)"
```

---

### Task B2.6: Disable Deep Research (B5)

**Files:**
- Modify: `worker/research/jobs.py` — replace job body with `raise FeatureDisabledError(...)`.
- Modify: `worker/research/service.py` — keep file but functions return immediately with disabled error.
- Modify: `api/routers/research.py` (or wherever `/api/repos/{id}/research` is defined) — return HTTP 503.
- Modify: `api/routers/research.py` (GET) — return HTTP 410 for new lookups (existing reports already in SQLite remain readable; gate by `report.created_at`).
- Modify: WebSocket handler — close immediately with code 1011.
- Modify: `cli/main.py` — `autowiki research` exits non-zero with the message.
- Modify: `api/routers/repos.py` (the `/api/repos/{id}` response) — add `features.deep_research = false`.
- Modify: `web/` — hide the Research entry point if `features.deep_research === false`.
- Modify: `tests/worker/test_deep_research.py` — `pytest.mark.skipif(...)` the existing tests.
- Add: `tests/api/test_research_disabled.py` — new test asserting 503 and CLI exit.
- Modify: `CLAUDE.md`, `docs/cli.md`, `docs/cli-zh.md`, `README.md` — note the disabled status.

- [ ] **Step 1: Test — endpoint returns 503**

```python
def test_post_research_returns_503(client):
    resp = client.post("/api/repos/abc/research", json={"question": "..."})
    assert resp.status_code == 503
    assert "temporarily unavailable" in resp.json()["detail"].lower()


def test_cli_research_exits_non_zero(runner):
    result = runner.invoke(app, ["research", "github.com/x/y", "..."])
    assert result.exit_code != 0
    assert "temporarily unavailable" in result.output.lower()
```

- [ ] **Step 2: Implement `FeatureDisabledError`**

In `worker/errors.py` (or `shared/errors.py`):

```python
class FeatureDisabledError(RuntimeError):
    """Raised when an indexing feature is temporarily disabled."""
```

- [ ] **Step 3: Update job + service + API + CLI**

Each surface returns the message: `"Deep Research is temporarily unavailable while migrating to keyword retrieval (see issue #TBD)."` — replace `#TBD` with the actual tracking issue once filed (Step 7 below).

- [ ] **Step 4: Update frontend feature flag**

In `api/routers/repos.py`, add to the `/api/repos/{id}` response:

```python
return RepositoryResponse(
    ...,
    features={"deep_research": False},
)
```

In `web/`, find the Research entry point and gate by `repo.features?.deep_research !== false`.

- [ ] **Step 5: Skip existing tests**

In `tests/worker/test_deep_research.py`:

```python
pytestmark = pytest.mark.skipif(
    True, reason="Deep Research disabled pending KeywordIndex migration (#TBD)"
)
```

- [ ] **Step 6: Update docs**

- `CLAUDE.md` API section: mark `/api/repos/{id}/research` as `(disabled — see issue #TBD)`.
- `docs/cli.md` and `docs/cli-zh.md`: same.
- `README.md`: feature list mentions Deep Research as "temporarily disabled, migrating to keyword retrieval."

- [ ] **Step 7: File the tracking issue**

Per spec §5.3 B5: file an issue titled "Deep Research: migrate to KeywordIndex + 1-hop graph expansion" before Layer B merges. Update every `#TBD` placeholder to the real issue number.

```bash
gh issue create --title "Deep Research: migrate to KeywordIndex + 1-hop graph expansion" --body "$(cat <<'EOF'
Deep Research currently depends on FAISSStore + EmbeddingProvider, which were removed in PR feat/section-drafting-and-stage4-removal (Layer B of the wiki page quality redesign). The feature is temporarily disabled with HTTP 503 / CLI non-zero exit / frontend hide.

Migration plan:
- Use KeywordIndex (worker/pipeline/retrieval/keyword_index.py) for per-step retrieval.
- Add 1-hop graph expansion via worker/pipeline/retrieval/repo_search.py (same pattern as fast-report).
- Re-enable surfaces.

Spec: docs/spec/claude/2026-04-29-wiki-page-quality-redesign.md §5.3 B5.
EOF
)"
```

- [ ] **Step 8: Run tests + commit**

```bash
uv run pytest tests/ --ignore=tests/e2e -q
git add -A
git commit -m "feat: temporarily disable Deep Research pending KeywordIndex migration (B5)"
```

---

### Task B2.End: Phase B acceptance + PR

- [ ] **Step 1: Run all tests + lint**

```bash
uv run pytest tests/ --ignore=tests/e2e -q
uv run ruff check . && uv run ruff format --check .
cd web && npm run lint && npm test
```

- [ ] **Step 2: Self-index without embedding key**

```bash
unset ANTHROPIC_API_KEY  # or whichever provider holds embedding
# (keep the LLM key for completion)
uv run autowiki index github.com/lazyxiang/AutoWiki --reuse-index=false
```

Verify:
- The job completes successfully.
- `~/.autowiki/repos/{hash}/ast/` contains exactly `repo_index.json` and `wiki_plan.json`.
- `~/.autowiki/repos/{hash}/faiss.index` and `faiss.meta.pkl` do not exist.

- [ ] **Step 3: Confirm Phase B acceptance criteria from spec §7**

Verify items 7–14. Document any miss in the PR description.

Specifically item 10:

```bash
grep -r "FAISSStore\|EmbeddingProvider" worker/
```

Expected: hits only inside `worker/embedding/` and `worker/research/`.

- [ ] **Step 4: Push + open PR**

```bash
git push -u origin feat/section-drafting-and-stage4-removal
gh pr create --title "feat(wiki): section drafting + Stage 4 removal + Deep Research disabled (Layer B)" --body "..."
```

PR body references spec §5.3 (B1, B2, B3, B4, B5) and spec §7 acceptance criteria 7–14.

---

## Phase Wrap-Up: CI self-index regression

After both PRs merge to `main`:

- [ ] **Step 1: Add CI job**

In `.github/workflows/ci.yml` (or equivalent), add a self-index regression job that runs:

```bash
uv run autowiki index . --reuse-index=false
uv run python scripts/check_self_index_acceptance.py
```

`scripts/check_self_index_acceptance.py` parses `~/.autowiki/repos/{hash}/ast/wiki_plan.json` and asserts the §5.2 file-list expectations and the §7 acceptance criteria.

- [ ] **Step 2: Tracking issue for the open questions**

Per spec §8, file follow-up issues for:
- Relevance scale (`int 1-10` chosen — close as resolved)
- Ownership timing retry budget (close as resolved — piggyback)
- Skeleton ownership (close as resolved — section-level only)
- Deep Research follow-up timing (link to the B5 tracking issue)

---

## Plan complete.

Plan saved to `docs/superpowers/plans/2026-05-01-wiki-page-quality-redesign.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
