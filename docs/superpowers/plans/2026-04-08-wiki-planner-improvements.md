# Wiki Planner & Generation Pipeline Improvements — Implementation Plan

> **Status: COMPLETE** — All 7 planned tasks implemented and merged to `feature/wiki-planner-improvements`. Additional scope items (Stage 7 removal, `--reuse-index`, per-phase validation, importance-ranked file capping) also implemented and committed.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Transform the wiki generation pipeline from a flat single-pass system into a hierarchical multi-agent generator with richer context, two-phase planning, semantic validation, and bottom-up page synthesis.

**Architecture:** Seven sequential improvements: (1) batch LLM generation, (2) enriched file summaries with dependency and docstring context, (3) BFS-seed sub-clustering for large dependency components, (4) dynamic page count heuristics, (5) two-phase outline-then-assign planning, (6) semantic plan validation, (7) bottom-up multi-agent page generation where parents synthesize child content.

**Tech Stack:** Python 3.12, asyncio, Tree-Sitter, FAISS, pytest (asyncio_mode=auto)

**Spec:** `docs/superpowers/specs/2026-04-08-wiki-planner-improvements-design.md`

## Additional scope (beyond original plan)

- **Stage 7 removed** (`diagram_synthesis.py`): redundant — the Overview page generator already emits an architecture Mermaid diagram via its prompt template. Pipeline is now 6 stages.
- **`--reuse-index` / `reuse_index`**: new bool param threaded from CLI → API `IndexRequest` → `enqueue_full_index` → `run_full_index`. When set, skips clearing and re-building the FAISS index (useful for re-running just the wiki planning/generation stages).
- **Per-phase validation** (`_validate_outline_structure`, `_validate_assignments`): validation now fires immediately after Phase 1 and Phase 2 respectively, triggering in-phase retries instead of waiting for the final `validate_wiki_plan()` call. The deferred Phase 3 retry loop and error-type classification helpers (`_OUTLINE_ERROR_PREFIXES`, `_is_outline_error`) were removed.
- **Importance-ranked file capping** (`_rank_files_by_importance`): when `to_llm_summary()` would exceed the file cap, the 200 most architecturally significant files are selected (scored by entity count, in-degree, entry-point name bonus, and shallowness) rather than the first 200 alphabetically.
- **`to_llm_summary` default changed to `max_files=200`**: calling without arguments is now safely bounded; pass `0` to opt in to the 800-file safety cap.
- **Dependency list truncation**: each file's import/external lists are capped at 10 entries with `+N more` suffix to prevent hub files from dominating the prompt budget.

---

### Task 1: Add `generate_batch` to LLMProvider

**Files:**
- Modify: `worker/llm/base.py:30-46` (LLMProvider ABC)
- Modify: `worker/llm/base.py:48-93` (LoggingLLMProvider)
- Test: `tests/worker/test_llm.py`

- [x] **Step 1: Write the failing tests**

Add to `tests/worker/test_llm.py`:

```python
async def test_generate_batch_default_impl():
    """Default generate_batch calls generate() for each prompt concurrently."""
    from unittest.mock import AsyncMock

    from worker.llm.base import LLMProvider

    # Create a concrete subclass with generate_batch inherited
    class FakeLLM(LLMProvider):
        async def generate(self, prompt: str, system: str = "") -> str:
            return f"response:{prompt}"

        async def generate_structured(self, prompt, schema, system=""):
            return {}

        async def generate_stream(self, prompt, system=""):
            yield ""

    llm = FakeLLM()
    results = await llm.generate_batch(["a", "b", "c"], system="sys")
    assert results == ["response:a", "response:b", "response:c"]


async def test_generate_batch_respects_max_concurrency():
    """At most max_concurrency calls run in parallel."""
    import asyncio

    from worker.llm.base import LLMProvider

    concurrent = 0
    max_seen = 0

    class TrackingLLM(LLMProvider):
        async def generate(self, prompt: str, system: str = "") -> str:
            nonlocal concurrent, max_seen
            concurrent += 1
            max_seen = max(max_seen, concurrent)
            await asyncio.sleep(0.01)
            concurrent -= 1
            return prompt

        async def generate_structured(self, prompt, schema, system=""):
            return {}

        async def generate_stream(self, prompt, system=""):
            yield ""

    llm = TrackingLLM()
    await llm.generate_batch([f"p{i}" for i in range(10)], max_concurrency=3)
    assert max_seen <= 3


async def test_logging_provider_wraps_generate_batch():
    """LoggingLLMProvider delegates generate_batch to inner provider."""
    from unittest.mock import AsyncMock

    from worker.llm.base import LoggingLLMProvider, LLMProvider

    class FakeLLM(LLMProvider):
        async def generate(self, prompt: str, system: str = "") -> str:
            return f"r:{prompt}"

        async def generate_structured(self, prompt, schema, system=""):
            return {}

        async def generate_stream(self, prompt, system=""):
            yield ""

    inner = FakeLLM()
    logged = LoggingLLMProvider(inner)
    results = await logged.generate_batch(["x", "y"])
    assert results == ["r:x", "r:y"]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_llm.py::test_generate_batch_default_impl tests/worker/test_llm.py::test_generate_batch_respects_max_concurrency tests/worker/test_llm.py::test_logging_provider_wraps_generate_batch -v`

Expected: FAIL — `generate_batch` does not exist yet.

- [x] **Step 3: Implement `generate_batch` on LLMProvider**

In `worker/llm/base.py`, add `import asyncio` at the top. Then add this method to the `LLMProvider` class after the `generate_stream` abstract method (after line 45):

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

        return list(await asyncio.gather(*[_one(p) for p in prompts]))
```

- [x] **Step 4: Implement `generate_batch` on LoggingLLMProvider**

Add this method to the `LoggingLLMProvider` class (after `generate_stream`):

```python
    async def generate_batch(
        self,
        prompts: list[str],
        system: str = "",
        max_concurrency: int = 5,
    ) -> list[str]:
        logger.debug(
            "LLM REQUEST (batch): %d prompts, system=%s",
            len(prompts),
            _truncate(system),
        )
        results = await self._provider.generate_batch(prompts, system, max_concurrency)
        logger.debug(
            "LLM RESPONSE (batch): %d responses, total %d chars",
            len(results),
            sum(len(r) for r in results),
        )
        return results
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/worker/test_llm.py -v`

Expected: All tests PASS including the 3 new ones.

- [x] **Step 6: Lint and commit**

```bash
uv run ruff check worker/llm/base.py tests/worker/test_llm.py
uv run ruff format worker/llm/base.py tests/worker/test_llm.py
git add worker/llm/base.py tests/worker/test_llm.py
git commit -m "feat: add generate_batch to LLMProvider with default asyncio.gather impl"
```

---

### Task 2: Enrich file summaries with dependency and docstring context

**Files:**
- Modify: `worker/pipeline/ast_analysis.py:389-434` (`FileAnalysis.to_llm_summary`)
- Test: `tests/worker/test_ast_analysis.py`

- [x] **Step 1: Write the failing tests**

Add to `tests/worker/test_ast_analysis.py`:

```python
def test_to_llm_summary_with_dep_graph(tmp_path):
    """to_llm_summary includes import/external deps and docstrings when dep_graph provided."""
    from worker.pipeline.dependency_graph import DependencyGraph

    f1 = tmp_path / "main.py"
    f1.write_text('"""Entry point for the app."""\nimport os\nfrom models import User\ndef run():\n    pass\n')
    f2 = tmp_path / "models.py"
    f2.write_text('class User:\n    """A user model."""\n    pass\n')

    result = analyze_all_files(tmp_path, [f1, f2])

    graph = DependencyGraph(
        edges={"main.py": ["models.py"]},
        clusters=[["main.py", "models.py"]],
        external_deps={"main.py": ["os"]},
    )
    summary = result.to_llm_summary(dep_graph=graph)

    # main.py should have import and external dep info
    lines = summary.splitlines()
    main_idx = next(i for i, l in enumerate(lines) if l.startswith("main.py"))
    # Next line(s) should include imports info
    dep_line = lines[main_idx + 1]
    assert "models.py" in dep_line
    assert "os" in dep_line

    # models.py should have docstring line
    models_idx = next(i for i, l in enumerate(lines) if l.startswith("models.py"))
    # Check that a docstring line exists within the next 2 lines
    docstring_found = any(
        "user model" in lines[models_idx + j].lower()
        for j in range(1, min(3, len(lines) - models_idx))
    )
    assert docstring_found


def test_to_llm_summary_no_limit_by_default(tmp_path):
    """max_files=0 (default) means no truncation."""
    for i in range(10):
        (tmp_path / f"mod{i}.py").write_text(f"def f{i}(): pass\n")

    files = list(tmp_path.glob("*.py"))
    result = analyze_all_files(tmp_path, files)
    summary = result.to_llm_summary()

    # All 10 files present, no truncation message
    assert "more files" not in summary
    for i in range(10):
        assert f"mod{i}.py" in summary


def test_to_llm_summary_safety_cap(tmp_path):
    """Files beyond 800 are listed as bare paths."""
    # Create a FileAnalysis with 802 entries directly to avoid disk I/O
    files = {}
    for i in range(802):
        rel = f"mod{i:04d}.py"
        files[rel] = FileInfo(
            rel_path=rel,
            entities=[{"type": "function", "name": f"f{i}", "start_line": 1, "end_line": 1}],
            class_count=0,
            function_count=1,
            summary=f"f{i}",
        )
    analysis = FileAnalysis(files=files)
    summary = analysis.to_llm_summary()

    # First 800 should have full detail
    assert "mod0000.py: 0 classes, 1 functions" in summary
    # Files beyond 800 should appear as bare paths
    assert "mod0800.py" in summary
    # The bare-path section should NOT have entity details
    last_lines = summary.splitlines()[-5:]
    # At least one bare path line should lack "classes"
    bare_lines = [l for l in last_lines if "mod080" in l and "classes" not in l]
    assert len(bare_lines) >= 1
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_ast_analysis.py::test_to_llm_summary_with_dep_graph tests/worker/test_ast_analysis.py::test_to_llm_summary_no_limit_by_default tests/worker/test_ast_analysis.py::test_to_llm_summary_safety_cap -v`

Expected: FAIL — `to_llm_summary` doesn't accept `dep_graph` yet, and default `max_files=200` truncates.

- [x] **Step 3: Implement enriched `to_llm_summary`**

Replace `to_llm_summary` method in `worker/pipeline/ast_analysis.py` (lines 389-434):

```python
    def to_llm_summary(
        self,
        max_files: int = 0,
        dep_graph: "DependencyGraph | None" = None,
    ) -> str:
        """Return per-file summaries with optional dependency and docstring context.

        Args:
            max_files: Maximum files with full detail. 0 means no limit (safety
                cap at 800). Files beyond the cap are listed as bare paths.
            dep_graph: Optional dependency graph for import/external dep info.
        """
        from worker.pipeline.dependency_graph import DependencyGraph  # noqa: F811

        sorted_keys = sorted(self.files.keys())
        cap = max_files if max_files > 0 else 800
        detailed = sorted_keys[:cap]
        overflow = sorted_keys[cap:]

        lines: list[str] = []
        for rel_path in detailed:
            info = self.files[rel_path]
            if not info.entities:
                lines.append(f"{rel_path}: (no named entities)")
            else:
                lines.append(
                    f"{rel_path}: {info.class_count} classes,"
                    f" {info.function_count} functions [{info.summary}]"
                )

            # Dependency line
            if dep_graph is not None:
                internal = dep_graph.edges.get(rel_path, [])
                external = dep_graph.external_deps.get(rel_path, [])
                if internal or external:
                    parts = []
                    if internal:
                        parts.append(f"imports: {', '.join(internal)}")
                    if external:
                        parts.append(f"external: {', '.join(external)}")
                    lines.append(f"  {' | '.join(parts)}")
                elif not info.entities:
                    pass  # already shows (no named entities)
                else:
                    lines.append("  (no dependencies)")

            # Docstring from first top-level entity
            if info.entities:
                for e in info.entities:
                    if e.get("docstring"):
                        doc = e["docstring"][:120].replace("\n", " ")
                        lines.append(f'  "{doc}"')
                        break

        # Overflow files as bare paths
        if overflow:
            lines.append(f"... and {len(overflow)} more files (paths only):")
            for rel_path in overflow:
                lines.append(f"  {rel_path}")

        return "\n".join(lines)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/worker/test_ast_analysis.py -v`

Expected: All tests PASS. Note: `test_analyze_all_files_to_llm_summary_truncation` passes `max_files=2` explicitly, so it still works.

- [x] **Step 5: Update callers to pass dep_graph**

In `worker/jobs.py`, find `file_analysis.to_llm_summary()` call (used in `_write_text_async` around line 451). This is just for debug output, not for the planner. The planner call happens inside `generate_wiki_plan` → `_build_prompt`. Update `_build_prompt` in `worker/pipeline/wiki_planner.py` to accept and pass dep_graph:

In `worker/pipeline/wiki_planner.py`, update `_build_prompt` signature (line 279) to add `dep_graph=None` parameter, and change the `file_summary` usage:

```python
def _build_prompt(
    file_summary: str,
    repo_name: str,
    readme: str | None = None,
    dep_info: str | None = None,
    clusters: list[list[str]] | None = None,
    all_files: list[str] | None = None,
) -> str:
```

No change needed here yet — the `file_summary` is already passed as a pre-formatted string. The dep_graph will be passed at the call site in `generate_wiki_plan` (line 543):

Change line 543 from:
```python
    file_summary = file_analysis.to_llm_summary()
```
to:
```python
    file_summary = file_analysis.to_llm_summary(dep_graph=dep_graph)
```

- [x] **Step 6: Run full test suite**

Run: `uv run pytest tests/ --ignore=tests/e2e -v`

Expected: All PASS.

- [x] **Step 7: Lint and commit**

```bash
uv run ruff check worker/pipeline/ast_analysis.py worker/pipeline/wiki_planner.py tests/worker/test_ast_analysis.py
uv run ruff format worker/pipeline/ast_analysis.py worker/pipeline/wiki_planner.py tests/worker/test_ast_analysis.py
git add worker/pipeline/ast_analysis.py worker/pipeline/wiki_planner.py tests/worker/test_ast_analysis.py
git commit -m "feat: enrich file summaries with dependency info and docstrings"
```

---

### Task 3: Dependency-aware sub-clustering

**Files:**
- Modify: `worker/pipeline/dependency_graph.py:308-366` (`_compute_clusters`, add `_split_large_cluster`)
- Modify: `worker/pipeline/dependency_graph.py:369-423` (`format_for_llm_prompt`)
- Test: `tests/worker/test_dependency_graph.py`

- [x] **Step 1: Write the failing tests**

Add to `tests/worker/test_dependency_graph.py`:

```python
from worker.pipeline.dependency_graph import _split_large_cluster


def test_split_large_cluster_small_cluster_unchanged():
    """Clusters within max_size are returned as-is."""
    cluster = ["a.py", "b.py", "c.py"]
    edges = {"a.py": ["b.py"], "b.py": ["c.py"]}
    result = _split_large_cluster(cluster, edges, max_size=15)
    assert len(result) == 1
    assert sorted(result[0]) == ["a.py", "b.py", "c.py"]


def test_split_large_cluster_splits_large():
    """Clusters exceeding max_size are split into sub-clusters."""
    # Create a chain: f0 -> f1 -> f2 -> ... -> f19
    files = [f"f{i}.py" for i in range(20)]
    edges = {f"f{i}.py": [f"f{i+1}.py"] for i in range(19)}
    result = _split_large_cluster(files, edges, max_size=8)
    assert len(result) >= 2
    # All files accounted for
    all_files = sorted(f for sub in result for f in sub)
    assert all_files == sorted(files)
    # Each sub-cluster respects max_size
    for sub in result:
        assert len(sub) <= 8


def test_split_large_cluster_disconnected_files():
    """Files with no edges still get assigned to sub-clusters."""
    files = [f"f{i}.py" for i in range(20)]
    edges = {}  # no edges at all
    result = _split_large_cluster(files, edges, max_size=10)
    all_files = sorted(f for sub in result for f in sub)
    assert all_files == sorted(files)
    for sub in result:
        assert len(sub) <= 10


def test_compute_clusters_splits_large_components(tmp_path):
    """_compute_clusters auto-splits components exceeding max_size=15."""
    # Create a 20-file connected component
    for i in range(20):
        content = f"from f{(i+1) % 20} import x\n" if i < 19 else "x = 1\n"
        (tmp_path / f"f{i}.py").write_text(content)

    files = [tmp_path / f"f{i}.py" for i in range(20)]
    graph = build_dependency_graph(files, tmp_path)

    # All sub-clusters should be <= 15
    for cluster in graph.clusters:
        assert len(cluster) <= 15
    # All files accounted for
    all_files = sorted(f for c in graph.clusters for f in c)
    assert len(all_files) == 20


def test_format_for_llm_prompt_default_500_cap(tmp_path):
    """Default max_edges is now 500 (was 150)."""
    # Create enough edges to exceed 150 but stay under 500
    for i in range(30):
        deps = "\n".join(f"from mod{j} import x" for j in range(i + 1, min(i + 8, 30)))
        (tmp_path / f"mod{i}.py").write_text(deps + "\nx = 1\n")

    files = list(tmp_path.glob("*.py"))
    graph = build_dependency_graph(files, tmp_path)
    total_edges = sum(len(d) for d in graph.edges.values())

    result = format_for_llm_prompt(graph)
    # With old default of 150, this would be truncated. With 500, it should not.
    if total_edges <= 500:
        assert "more edges not shown" not in result
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_dependency_graph.py::test_split_large_cluster_small_cluster_unchanged tests/worker/test_dependency_graph.py::test_split_large_cluster_splits_large tests/worker/test_dependency_graph.py::test_split_large_cluster_disconnected_files tests/worker/test_dependency_graph.py::test_compute_clusters_splits_large_components tests/worker/test_dependency_graph.py::test_format_for_llm_prompt_default_500_cap -v`

Expected: FAIL — `_split_large_cluster` doesn't exist.

- [x] **Step 3: Implement `_split_large_cluster`**

Add this function in `worker/pipeline/dependency_graph.py` before `_compute_clusters` (before line 308):

```python
def _split_large_cluster(
    cluster: list[str],
    edges: dict[str, list[str]],
    max_size: int = 15,
) -> list[list[str]]:
    """Split a large cluster into sub-clusters using BFS-seed grouping.

    Picks the file with the most import edges as the first seed, BFS outward
    to fill a sub-cluster up to max_size, then repeats with remaining files.
    """
    if len(cluster) <= max_size:
        return [sorted(cluster)]

    cluster_set = set(cluster)
    # Build sub-graph adjacency (undirected for BFS)
    adj: dict[str, list[str]] = {f: [] for f in cluster}
    for src in cluster:
        for tgt in edges.get(src, []):
            if tgt in cluster_set:
                adj[src].append(tgt)
                adj[tgt].append(src)

    remaining = set(cluster)
    sub_clusters: list[list[str]] = []

    while remaining:
        # Pick seed: file with most edges among remaining
        seed = max(remaining, key=lambda f: len([n for n in adj.get(f, []) if n in remaining]))
        # BFS from seed
        visited: list[str] = []
        queue = [seed]
        seen = {seed}
        while queue and len(visited) < max_size:
            node = queue.pop(0)
            if node not in remaining:
                continue
            visited.append(node)
            remaining.discard(node)
            for neighbor in adj.get(node, []):
                if neighbor in remaining and neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)

        sub_clusters.append(sorted(visited))

    return sub_clusters
```

- [x] **Step 4: Modify `_compute_clusters` to use `_split_large_cluster`**

Replace the return statement in `_compute_clusters` (line 366):

From:
```python
    return [sorted(g) for g in sorted(groups.values(), key=lambda g: (-len(g), g[0]))]
```

To:
```python
    raw_clusters = sorted(groups.values(), key=lambda g: (-len(g), g[0]))
    result: list[list[str]] = []
    for g in raw_clusters:
        result.extend(_split_large_cluster(sorted(g), edges))
    return result
```

- [x] **Step 5: Change `format_for_llm_prompt` default `max_edges`**

In `worker/pipeline/dependency_graph.py`, change the signature of `format_for_llm_prompt` (line 369):

From:
```python
def format_for_llm_prompt(graph: DependencyGraph, max_edges: int = 150) -> str:
```

To:
```python
def format_for_llm_prompt(graph: DependencyGraph, max_edges: int = 500) -> str:
```

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/worker/test_dependency_graph.py -v`

Expected: All PASS.

- [x] **Step 7: Lint and commit**

```bash
uv run ruff check worker/pipeline/dependency_graph.py tests/worker/test_dependency_graph.py
uv run ruff format worker/pipeline/dependency_graph.py tests/worker/test_dependency_graph.py
git add worker/pipeline/dependency_graph.py tests/worker/test_dependency_graph.py
git commit -m "feat: add BFS-seed sub-clustering for large dependency components"
```

---

### Task 4: Dynamic page count heuristics

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` (add `_suggest_page_range`)
- Test: `tests/worker/test_wiki_planner.py`

- [x] **Step 1: Write the failing tests**

Add to `tests/worker/test_wiki_planner.py`:

```python
from worker.pipeline.wiki_planner import _suggest_page_range


def test_suggest_page_range_small_repo():
    assert _suggest_page_range(5, 10) == (3, 6)


def test_suggest_page_range_medium_repo_few_entities():
    assert _suggest_page_range(20, 30) == (5, 12)


def test_suggest_page_range_medium_repo_many_entities():
    assert _suggest_page_range(25, 80) == (8, 15)


def test_suggest_page_range_large_repo_few_entities():
    assert _suggest_page_range(60, 100) == (10, 25)


def test_suggest_page_range_large_repo_many_entities():
    assert _suggest_page_range(80, 200) == (15, 35)


def test_suggest_page_range_very_large_repo():
    assert _suggest_page_range(200, 500) == (20, 50)


def test_suggest_page_range_huge_repo():
    assert _suggest_page_range(500, 1000) == (30, 70)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_wiki_planner.py::test_suggest_page_range_small_repo -v`

Expected: FAIL — `_suggest_page_range` doesn't exist.

- [x] **Step 3: Implement `_suggest_page_range`**

Add this function in `worker/pipeline/wiki_planner.py` after `_slugify_title` (after line 44):

```python
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
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/worker/test_wiki_planner.py -v`

Expected: All PASS.

- [x] **Step 5: Lint and commit**

```bash
uv run ruff check worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
uv run ruff format worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat: add dynamic page count heuristics for wiki planner"
```

---

### Task 5: Two-phase planning (outline + file assignment)

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` (replace `_build_prompt`, `generate_wiki_plan`; add `_generate_outline`, `_assign_files`, `_build_outline_prompt`, `_build_assignment_prompt`)
- Test: `tests/worker/test_wiki_planner.py`

This is the largest task. We rewrite the core planning logic.

- [x] **Step 1: Write the failing tests for Phase 1 (outline generation)**

Add to `tests/worker/test_wiki_planner.py`:

```python
async def test_generate_outline(mock_llm):
    """_generate_outline returns a list of page dicts with title/purpose/parent."""
    from worker.pipeline.wiki_planner import _generate_outline

    mock_llm.generate_structured.return_value = {
        "pages": [
            {"title": "Overview", "purpose": "Top-level overview."},
            {"title": "API", "purpose": "REST API.", "parent": "Overview"},
            {"title": "Worker", "purpose": "Background jobs.", "parent": "Overview"},
        ]
    }
    outline = await _generate_outline(
        file_summary="main.py: 0 classes, 1 functions [run]",
        repo_name="test",
        llm=mock_llm,
        readme="A test project.",
        dep_info=None,
        clusters=None,
        page_range=(3, 10),
        system="You are a planner.",
        on_retry=None,
    )
    assert len(outline) == 3
    assert outline[0]["title"] == "Overview"
    assert outline[1].get("parent") == "Overview"
```

- [x] **Step 2: Write the failing tests for Phase 2 (file assignment)**

Add to `tests/worker/test_wiki_planner.py`:

```python
async def test_assign_files(mock_llm):
    """_assign_files returns a dict mapping page titles to file lists."""
    from worker.pipeline.wiki_planner import _assign_files

    mock_llm.generate_structured.return_value = {
        "assignments": [
            {"file": "main.py", "page_title": "Overview"},
            {"file": "api.py", "page_title": "API"},
            {"file": "worker.py", "page_title": "Worker"},
        ]
    }
    outline = [
        {"title": "Overview", "purpose": "Top-level."},
        {"title": "API", "purpose": "REST API."},
        {"title": "Worker", "purpose": "Jobs."},
    ]
    result = await _assign_files(
        outline=outline,
        file_summary="main.py: ...\napi.py: ...\nworker.py: ...",
        dep_info=None,
        all_files=["main.py", "api.py", "worker.py"],
        llm=mock_llm,
        system="Assign files.",
        on_retry=None,
    )
    assert result["Overview"] == ["main.py"]
    assert result["API"] == ["api.py"]
    assert result["Worker"] == ["worker.py"]


async def test_assign_files_orphans_distributed(mock_llm):
    """Files assigned to unknown pages get redistributed."""
    from worker.pipeline.wiki_planner import _assign_files

    mock_llm.generate_structured.return_value = {
        "assignments": [
            {"file": "main.py", "page_title": "Overview"},
            {"file": "orphan.py", "page_title": "NonExistent"},
        ]
    }
    outline = [{"title": "Overview", "purpose": "Top."}]
    result = await _assign_files(
        outline=outline,
        file_summary="main.py: ...\norphan.py: ...",
        dep_info=None,
        all_files=["main.py", "orphan.py"],
        llm=mock_llm,
        system="Assign.",
        on_retry=None,
    )
    # orphan.py should be assigned to Overview (first page)
    assert "orphan.py" in result["Overview"]
```

- [x] **Step 3: Write the failing test for the updated orchestrator**

Add to `tests/worker/test_wiki_planner.py`:

```python
async def test_generate_wiki_plan_two_phase(mock_llm):
    """generate_wiki_plan uses two-phase planning."""
    # Phase 1 returns outline, Phase 2 returns assignments
    mock_llm.generate_structured.side_effect = [
        # Phase 1: outline
        {
            "pages": [
                {"title": "Overview", "purpose": "Top-level overview."},
                {"title": "Models", "purpose": "Data models."},
            ]
        },
        # Phase 2: file assignment
        {
            "assignments": [
                {"file": "main.py", "page_title": "Overview"},
                {"file": "models.py", "page_title": "Models"},
            ]
        },
    ]

    file_analysis = FileAnalysis(
        files={
            "main.py": FileInfo(rel_path="main.py", entities=[], summary=""),
            "models.py": FileInfo(rel_path="models.py", entities=[], summary=""),
        }
    )
    plan = await generate_wiki_plan(file_analysis, repo_name="test", llm=mock_llm)
    assert len(plan.pages) == 2
    assert {p.title for p in plan.pages} == {"Overview", "Models"}
    assert plan.pages[0].files == ["main.py"]
    assert plan.pages[1].files == ["models.py"]
```

- [x] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_wiki_planner.py::test_generate_outline tests/worker/test_wiki_planner.py::test_assign_files tests/worker/test_wiki_planner.py::test_assign_files_orphans_distributed tests/worker/test_wiki_planner.py::test_generate_wiki_plan_two_phase -v`

Expected: FAIL — functions don't exist.

- [x] **Step 5: Implement new schemas and prompt builders**

In `worker/pipeline/wiki_planner.py`, replace `_WIKI_PLAN_SCHEMA` (lines 233-251) and `_build_prompt` (lines 279-358) with:

```python
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


def _build_outline_prompt(
    file_summary: str,
    repo_name: str,
    readme: str | None = None,
    dep_info: str | None = None,
    clusters: list[list[str]] | None = None,
    page_range: tuple[int, int] = (5, 20),
) -> str:
    """Build the Phase 1 prompt: generate page tree without file assignments."""
    sections = [f"Repository: {repo_name}"]

    if readme:
        sections.append(f"README:\n{readme}")

    sections.append(f"File summaries:\n{file_summary}")

    if dep_info:
        sections.append(f"Dependency relationships:\n{dep_info}")

    if clusters:
        cluster_strs = [
            f"  Cluster {i + 1}: {', '.join(c)}"
            for i, c in enumerate(clusters[:30])
        ]
        if len(clusters) > 30:
            cluster_strs.append(f"  ... and {len(clusters) - 30} more clusters")
        sections.append(
            "File clusters (files that import each other):\n"
            + "\n".join(cluster_strs)
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
```

- [x] **Step 6: Implement `_generate_outline`**

Add after the prompt builders:

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
    prompt = _build_outline_prompt(
        file_summary=file_summary,
        repo_name=repo_name,
        readme=readme,
        dep_info=dep_info,
        clusters=clusters,
        page_range=page_range,
    )

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
            return pages
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."

    raise ValueError("Failed to generate outline after all retries")
```

- [x] **Step 7: Implement `_assign_files`**

Add after `_generate_outline`:

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
) -> dict[str, list[str]]:
    """Phase 2: Assign every file to a page. Returns {page_title: [files]}."""
    prompt = _build_assignment_prompt(
        outline=outline,
        file_summary=file_summary,
        dep_info=dep_info,
        all_files=all_files,
    )

    valid_titles = {p["title"] for p in outline}
    first_title = outline[0]["title"]

    for attempt in range(max_retries):
        try:
            raw = await async_retry(
                llm.generate_structured,
                prompt,
                schema=_ASSIGNMENT_SCHEMA,
                system=system,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
            assignments = raw.get("assignments", [])
            result: dict[str, list[str]] = {p["title"]: [] for p in outline}
            assigned_files: set[str] = set()

            for a in assignments:
                f = a.get("file", "")
                title = a.get("page_title", "")
                if f in all_files and f not in assigned_files:
                    target = title if title in valid_titles else first_title
                    result[target].append(f)
                    assigned_files.add(f)

            # Assign any unassigned files to the first page
            for f in all_files:
                if f not in assigned_files:
                    result[first_title].append(f)

            return result

        except (ValueError, json.JSONDecodeError, KeyError) as e:
            if attempt < max_retries - 1:
                prompt += f"\n\nPrevious attempt failed: {e}. Please fix and retry."

    # Fallback: round-robin distribution
    result = {p["title"]: [] for p in outline}
    titles = [p["title"] for p in outline]
    for i, f in enumerate(sorted(all_files)):
        result[titles[i % len(titles)]].append(f)
    return result
```

- [x] **Step 8: Rewrite `generate_wiki_plan` to use two-phase approach**

Replace the `generate_wiki_plan` function body (lines 471-611). Keep the signature but update the implementation:

```python
async def generate_wiki_plan(
    file_analysis,
    repo_name: str,
    llm: LLMProvider,
    dep_graph=None,
    max_retries: int = 3,
    readme: str | None = None,
    on_retry: OnRetryCallback | None = None,
    existing_titles: set[str] | None = None,
    wiki_language: str = "en",
) -> WikiPlan:
    """Generate a hierarchical wiki plan using two-phase LLM planning."""
    from worker.pipeline.dependency_graph import format_for_llm_prompt

    file_summary = file_analysis.to_llm_summary(dep_graph=dep_graph)
    all_files = list(file_analysis.files.keys())
    dep_info = format_for_llm_prompt(dep_graph) if dep_graph is not None else None
    clusters = dep_graph.clusters if dep_graph is not None else None

    # Compute entity count for page range heuristic
    entity_count = sum(
        len(info.entities) for info in file_analysis.files.values()
    )
    page_range = _suggest_page_range(len(all_files), entity_count)

    system = _SYSTEM + get_planner_language_instruction(wiki_language)

    # Phase 1: Generate outline
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
        )
    except ValueError:
        # Fallback to cluster-based plan
        return _fallback_plan(repo_name, all_files, clusters)

    # Phase 2: Assign files to pages
    file_assignments = await _assign_files(
        outline=outline,
        file_summary=file_summary,
        dep_info=dep_info,
        all_files=all_files,
        llm=llm,
        system=system,
        on_retry=on_retry,
        max_retries=max_retries,
    )

    # Merge outline + assignments into WikiPlan
    pages = []
    for p in outline:
        title = p["title"]
        parent = p.get("parent")
        # Validate parent against known titles
        all_known = {pp["title"] for pp in outline} | (existing_titles or set())
        if parent and parent not in all_known:
            parent = None
        pages.append(
            WikiPageSpec(
                title=title,
                purpose=p["purpose"],
                parent=parent,
                files=file_assignments.get(title, []),
            )
        )

    return WikiPlan(pages=pages)


def _fallback_plan(
    repo_name: str,
    all_files: list[str],
    clusters: list[list[str]] | None,
) -> WikiPlan:
    """Build a flat cluster-based fallback plan when LLM planning fails."""
    fallback_pages = [
        WikiPageSpec(
            title="Overview",
            purpose=f"High-level overview of the {repo_name} project architecture and components.",
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
```

- [x] **Step 9: Remove old `_WIKI_PLAN_SCHEMA` and `_build_prompt`**

Delete the old `_WIKI_PLAN_SCHEMA` (lines 233-251) and `_build_prompt` function (lines 279-358) — they have been replaced by the phase-specific schemas and prompt builders.

- [x] **Step 10: Update `conftest.py` mock to support two-phase calls**

The mock_llm in `tests/conftest.py` returns a fixed dict for `generate_structured`. With two-phase planning, it gets called twice with different schemas. Update:

```python
@pytest.fixture
def mock_llm():
    """Returns a mock LLMProvider that returns predictable content."""
    m = AsyncMock()
    m.generate.return_value = "Mocked wiki page content."
    m.generate_structured.side_effect = [
        # Phase 1: outline
        {
            "pages": [
                {"title": "Overview", "purpose": "High-level overview of the project architecture."},
                {"title": "Models", "purpose": "Data models including User and Post classes."},
                {"title": "Utils", "purpose": "Utility functions for greeting and validation."},
            ]
        },
        # Phase 2: file assignment
        {
            "assignments": [
                {"file": "main.py", "page_title": "Overview"},
                {"file": "models.py", "page_title": "Models"},
                {"file": "utils.py", "page_title": "Utils"},
            ]
        },
    ]
    m.generate_batch.return_value = ["Mocked wiki page content."]
    return m
```

**Important**: `side_effect` with a list is consumed sequentially. Tests that call `generate_structured` multiple times will pop from this list. Tests that only call it once (like LLM provider tests) need to set their own return value/side_effect. Check existing tests — `test_anthropic_generate_structured_returns_dict` patches `provider._client.messages.create` directly, so it's unaffected. The wiki planner test `test_generate_wiki_plan` uses `mock_llm` which now has `side_effect` — it will consume both items correctly.

However, `side_effect` as a list is consumed and then raises `StopIteration`. For tests that re-use `mock_llm` across multiple test functions, each test function gets a fresh fixture (pytest creates new fixtures per test). So this is safe.

But tests like `test_full_index_job_updates_status` call `run_full_index` which calls `generate_wiki_plan` (consumes 2 items) then `generate_page` (calls `generate` not `generate_structured`) ~~then `synthesize_diagrams` (calls `generate_structured` once more — but `side_effect` list is exhausted)~~. **Note: `synthesize_diagrams` no longer exists — Stage 7 was removed.**

Change the fixture to use a callable `side_effect` instead:

```python
@pytest.fixture
def mock_llm():
    """Returns a mock LLMProvider that returns predictable content."""
    m = AsyncMock()
    m.generate.return_value = "Mocked wiki page content."

    _structured_responses = iter([
        # Phase 1: outline
        {
            "pages": [
                {"title": "Overview", "purpose": "High-level overview of the project architecture."},
                {"title": "Models", "purpose": "Data models including User and Post classes."},
                {"title": "Utils", "purpose": "Utility functions for greeting and validation."},
            ]
        },
        # Phase 2: file assignment
        {
            "assignments": [
                {"file": "main.py", "page_title": "Overview"},
                {"file": "models.py", "page_title": "Models"},
                {"file": "utils.py", "page_title": "Utils"},
            ]
        },
    ])

    _default_structured = {
        "pages": [
            {"title": "Overview", "purpose": "Fallback.", "files": ["main.py"]},
        ]
    }

    async def _structured_side_effect(*args, **kwargs):
        try:
            return next(_structured_responses)
        except StopIteration:
            return _default_structured

    m.generate_structured.side_effect = _structured_side_effect
    m.generate_batch.return_value = ["Mocked wiki page content."]
    return m
```

- [x] **Step 11: Run full test suite**

Run: `uv run pytest tests/ --ignore=tests/e2e -v`

Expected: All PASS. If integration tests fail due to mock changes, adjust the mock responses.

- [x] **Step 12: Lint and commit**

```bash
uv run ruff check worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py tests/conftest.py
uv run ruff format worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py tests/conftest.py
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py tests/conftest.py
git commit -m "feat: implement two-phase wiki planning (outline + file assignment)"
```

---

### Task 6: Enhanced plan validation

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` (`validate_wiki_plan`)
- Test: `tests/worker/test_wiki_planner.py`

- [x] **Step 1: Write the failing tests**

Add to `tests/worker/test_wiki_planner.py`:

```python
def test_validate_rejects_page_over_25_files():
    raw = {
        "pages": [
            {"title": "Mega Page", "purpose": "Too many files.", "files": [f"f{i}.py" for i in range(30)]},
        ]
    }
    with pytest.raises(ValueError, match="split into focused sub-pages"):
        validate_wiki_plan(raw)


def test_validate_rejects_empty_non_overview_page():
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top.", "files": ["main.py"]},
            {"title": "Empty Page", "purpose": "Nothing here.", "files": []},
        ]
    }
    with pytest.raises(ValueError, match="no files assigned"):
        validate_wiki_plan(raw)


def test_validate_allows_empty_overview_page():
    """Overview page with 0 files is allowed (orphans get assigned to it)."""
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top.", "files": []},
            {"title": "API", "purpose": "Endpoints.", "files": ["api.py"]},
        ]
    }
    plan = validate_wiki_plan(raw)
    assert len(plan.pages) == 2


def test_validate_rejects_too_deep_hierarchy():
    raw = {
        "pages": [
            {"title": "L0", "purpose": ".", "files": ["a.py"]},
            {"title": "L1", "purpose": ".", "parent": "L0", "files": ["b.py"]},
            {"title": "L2", "purpose": ".", "parent": "L1", "files": ["c.py"]},
            {"title": "L3", "purpose": ".", "parent": "L2", "files": ["d.py"]},
            {"title": "L4", "purpose": ".", "parent": "L3", "files": ["e.py"]},
        ]
    }
    with pytest.raises(ValueError, match="flatten to at most 4 levels"):
        validate_wiki_plan(raw)


def test_validate_rejects_flat_plan_for_large_repo():
    raw = {
        "pages": [
            {"title": "Page1", "purpose": ".", "files": [f"f{i}.py" for i in range(20)]},
            {"title": "Page2", "purpose": ".", "files": [f"g{i}.py" for i in range(15)]},
        ]
    }
    all_files = [f"f{i}.py" for i in range(20)] + [f"g{i}.py" for i in range(15)]
    with pytest.raises(ValueError, match="create 2-3 levels of hierarchy"):
        validate_wiki_plan(raw, all_files=all_files)


def test_validate_rejects_too_few_pages():
    raw = {
        "pages": [
            {"title": "Overview", "purpose": ".", "files": [f"f{i}.py" for i in range(25)]},
        ]
    }
    with pytest.raises(ValueError, match="create more granular pages"):
        validate_wiki_plan(raw, page_range=(5, 20))
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_wiki_planner.py::test_validate_rejects_page_over_25_files tests/worker/test_wiki_planner.py::test_validate_rejects_empty_non_overview_page tests/worker/test_wiki_planner.py::test_validate_rejects_too_deep_hierarchy tests/worker/test_wiki_planner.py::test_validate_rejects_flat_plan_for_large_repo tests/worker/test_wiki_planner.py::test_validate_rejects_too_few_pages -v`

Expected: FAIL — current validation doesn't check these.

- [x] **Step 3: Implement enhanced validation**

Update `validate_wiki_plan` in `worker/pipeline/wiki_planner.py`. Add new parameters and checks after the existing structural checks (orphan file assignment). Insert the semantic checks before the `return WikiPlan(pages=pages)` line:

Update the function signature:

```python
def validate_wiki_plan(
    raw: dict,
    all_files: list[str] | None = None,
    existing_titles: set[str] | None = None,
    clusters: list[list[str]] | None = None,
    page_range: tuple[int, int] | None = None,
) -> WikiPlan:
```

After the orphan-files block and before `return WikiPlan(pages=pages)`, add:

```python
    # ── Semantic validation ──────────────────────────────────────────────
    import logging as _logging

    _log = _logging.getLogger("worker.planner")

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
        seen: set[str] = set()
        while title_to_parent.get(current) is not None:
            current = title_to_parent[current]
            if current in seen:
                break
            seen.add(current)
            d += 1
        return d

    max_depth = max((_depth(p.title) for p in pages), default=1)
    if max_depth > 4:
        raise ValueError(
            f"Wiki hierarchy is {max_depth} levels deep — "
            "flatten to at most 4 levels"
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
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            page_titles_for_cluster: set[str] = set()
            for f in cluster:
                for p in pages:
                    if f in p.files:
                        page_titles_for_cluster.add(p.title)
                        break
            if len(page_titles_for_cluster) > 3:
                _log.warning(
                    "Cluster files [%s...] scattered across %d pages: %s",
                    cluster[0],
                    len(page_titles_for_cluster),
                    ", ".join(sorted(page_titles_for_cluster)),
                )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/worker/test_wiki_planner.py -v`

Expected: All PASS. Existing tests should still pass — they have ≤3 files per page, valid hierarchy, etc.

- [x] **Step 5: Run full test suite**

Run: `uv run pytest tests/ --ignore=tests/e2e -v`

Expected: All PASS.

- [x] **Step 6: Lint and commit**

```bash
uv run ruff check worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
uv run ruff format worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat: add semantic validation rules to wiki plan validator"
```

---

### Task 7: Multi-agent bottom-up page generation

**Files:**
- Modify: `worker/pipeline/page_generator.py` (add `compute_generation_order`, `generate_page_batch`, update `generate_page` and prompts)
- Modify: `worker/jobs.py` (replace flat loop in `run_full_index` and `run_refresh_index`)
- Test: `tests/worker/test_page_generator.py`
- Test: `tests/worker/test_jobs.py`

This is the most complex task. We'll break it into sub-steps.

- [x] **Step 1: Write the failing test for `compute_generation_order`**

Add to `tests/worker/test_page_generator.py`:

```python
from worker.pipeline.page_generator import compute_generation_order
from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan


def test_compute_generation_order_single_level():
    plan = WikiPlan(pages=[
        WikiPageSpec(title="A", purpose=".", files=["a.py"]),
        WikiPageSpec(title="B", purpose=".", files=["b.py"]),
    ])
    levels = compute_generation_order(plan)
    assert len(levels) == 1
    assert {p.title for p in levels[0]} == {"A", "B"}


def test_compute_generation_order_two_levels():
    plan = WikiPlan(pages=[
        WikiPageSpec(title="Root", purpose=".", files=["r.py"]),
        WikiPageSpec(title="Child1", purpose=".", parent="Root", files=["c1.py"]),
        WikiPageSpec(title="Child2", purpose=".", parent="Root", files=["c2.py"]),
    ])
    levels = compute_generation_order(plan)
    # Deepest first: children first, then root
    assert len(levels) == 2
    assert {p.title for p in levels[0]} == {"Child1", "Child2"}
    assert {p.title for p in levels[1]} == {"Root"}


def test_compute_generation_order_three_levels():
    plan = WikiPlan(pages=[
        WikiPageSpec(title="Root", purpose=".", files=["r.py"]),
        WikiPageSpec(title="Mid", purpose=".", parent="Root", files=["m.py"]),
        WikiPageSpec(title="Leaf", purpose=".", parent="Mid", files=["l.py"]),
    ])
    levels = compute_generation_order(plan)
    assert len(levels) == 3
    assert levels[0][0].title == "Leaf"
    assert levels[1][0].title == "Mid"
    assert levels[2][0].title == "Root"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_page_generator.py::test_compute_generation_order_single_level tests/worker/test_page_generator.py::test_compute_generation_order_two_levels tests/worker/test_page_generator.py::test_compute_generation_order_three_levels -v`

Expected: FAIL — function doesn't exist.

- [x] **Step 3: Implement `compute_generation_order`**

Add to `worker/pipeline/page_generator.py` after the imports:

```python
from worker.pipeline.wiki_planner import WikiPlan


def compute_generation_order(plan: WikiPlan) -> list[list[WikiPageSpec]]:
    """Return pages grouped by depth level, deepest first.

    Pages at the same depth have no parent-child relationship and can be
    generated in parallel. Returns [[deepest], ..., [roots]].
    """
    # Build title -> depth map
    title_to_page = {p.title: p for p in plan.pages}
    depths: dict[str, int] = {}

    def _get_depth(title: str) -> int:
        if title in depths:
            return depths[title]
        page = title_to_page.get(title)
        if page is None or page.parent is None or page.parent not in title_to_page:
            depths[title] = 0
            return 0
        d = _get_depth(page.parent) + 1
        depths[title] = d
        return d

    for p in plan.pages:
        _get_depth(p.title)

    # Group by depth
    max_depth = max(depths.values(), default=0)
    levels: list[list[WikiPageSpec]] = []
    for d in range(max_depth, -1, -1):
        level = [p for p in plan.pages if depths.get(p.title, 0) == d]
        if level:
            levels.append(level)

    return levels
```

- [x] **Step 4: Run the generation order tests**

Run: `uv run pytest tests/worker/test_page_generator.py::test_compute_generation_order_single_level tests/worker/test_page_generator.py::test_compute_generation_order_two_levels tests/worker/test_page_generator.py::test_compute_generation_order_three_levels -v`

Expected: PASS.

- [x] **Step 5: Write the failing test for parent page generation with child content**

Add to `tests/worker/test_page_generator.py`:

```python
async def test_generate_page_with_child_contents(mock_llm, mock_embedding):
    import tempfile

    import numpy as np

    from worker.pipeline.rag_indexer import FAISSStore

    with tempfile.TemporaryDirectory() as tmp:
        store = FAISSStore(
            dimension=1536,
            index_path=Path(tmp) / "idx",
            meta_path=Path(tmp) / "meta.pkl",
        )
        store.add(
            [np.zeros(1536, dtype=np.float32)],
            [{"text": "class App: pass", "file": "app.py", "start_line": 1, "end_line": 1}],
        )

        parent_spec = WikiPageSpec(
            title="System Overview",
            purpose="Top-level architecture.",
            files=["app.py"],
        )
        child_results = [
            PageResult(slug="api", title="API Layer", content="## API\nHandles HTTP requests."),
            PageResult(slug="worker", title="Worker", content="## Worker\nProcesses background jobs."),
        ]
        result = await generate_page(
            parent_spec, store, mock_llm, mock_embedding,
            repo_name="test",
            child_contents=child_results,
        )
    assert isinstance(result, PageResult)
    assert result.slug == "system-overview"
    # The mock returns "Mocked wiki page content." but the important thing
    # is that generate() was called with a prompt containing child content
    call_args = mock_llm.generate.call_args
    prompt = call_args[0][0]
    assert "API Layer" in prompt
    assert "Worker" in prompt
    assert "Handles HTTP requests" in prompt
```

- [x] **Step 6: Run the test to verify it fails**

Run: `uv run pytest tests/worker/test_page_generator.py::test_generate_page_with_child_contents -v`

Expected: FAIL — `generate_page` doesn't accept `child_contents`.

- [x] **Step 7: Update `generate_page` to accept child contents**

In `worker/pipeline/page_generator.py`, update `generate_page` signature to add `child_contents`:

```python
async def generate_page(
    spec: WikiPageSpec,
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    top_k: int = 12,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
    child_contents: list[PageResult] | None = None,
) -> PageResult:
```

- [x] **Step 8: Add parent-specific prompt template**

Add after `_SYSTEM` in `worker/pipeline/page_generator.py`:

```python
_PARENT_TEMPLATE = (
    'Write a wiki page for "{title}" that serves as the entry point '
    "for its child pages. Structure:\n\n"
    "## Overview\n"
    "What this subsystem/area does and why it exists. "
    "High-level narrative.\n\n"
    "## Architecture\n"
    "How the child components fit together. Include a Mermaid "
    "diagram showing the relationships and data flow between "
    "child components.\n\n"
    "## Key Design Decisions\n"
    "Important architectural choices that span multiple child "
    "components.\n\n"
    "## How It Works\n"
    "End-to-end flow tying the child components together.\n\n"
    "Do NOT duplicate content from child pages — reference "
    "them by name.\n"
    "Output Markdown only."
)
```

- [x] **Step 9: Update `_build_page_prompt` to include child content**

Update `_build_page_prompt` to accept and use child contents:

```python
def _build_page_prompt(
    spec: WikiPageSpec,
    context_chunks: list[dict],
    repo_name: str,
    dep_info: dict[str, Any] | None = None,
    entity_details: list[dict[str, Any]] | None = None,
    child_contents: list[PageResult] | None = None,
) -> str:
```

In the function body, before the `is_overview` check, add:

```python
    # Child page content for parent pages
    if child_contents:
        child_sections = []
        for child in child_contents:
            child_sections.append(f"### Child: \"{child.title}\"\n{child.content}")
        sections.append(
            "## Child Pages (already generated)\n"
            "The following child pages have been written. Your role is to "
            "SYNTHESIZE and CONNECT — provide the high-level narrative, "
            "explain how these components relate, and add context that "
            "individual pages cannot provide. Do NOT repeat details covered "
            "in child pages; reference them instead.\n\n"
            + "\n\n".join(child_sections)
        )
```

Then update the instruction block selection. Replace the `is_overview` / `else` block with:

```python
    has_children = bool(child_contents)
    is_overview = spec.slug == "overview" or "overview" in spec.title.lower()

    if has_children:
        sections.append(_PARENT_TEMPLATE.format(title=spec.title))
    elif is_overview:
        sections.append(
            # ... existing overview template unchanged ...
        )
    else:
        sections.append(
            # ... existing component template unchanged ...
        )
```

- [x] **Step 10: Update `generate_page` to pass child_contents through**

In the `generate_page` function body, pass `child_contents` to `_build_page_prompt`:

```python
    prompt = _build_page_prompt(
        spec, context_chunks, repo_name, dep_info, entity_details,
        child_contents=child_contents,
    )
```

- [x] **Step 11: Implement `generate_page_batch`**

Add to `worker/pipeline/page_generator.py`:

```python
async def generate_page_batch(
    specs_with_children: list[tuple[WikiPageSpec, list[PageResult] | None]],
    store: FAISSStore,
    llm: LLMProvider,
    embedding: EmbeddingProvider,
    repo_name: str,
    file_analysis: "FileAnalysis",
    dep_graph: "DependencyGraph",
    on_retry: OnRetryCallback | None = None,
    wiki_language: str = "en",
) -> list[PageResult]:
    """Generate all pages in a batch using llm.generate_batch()."""
    from worker.pipeline.dependency_graph import summarize_page_deps

    prompts: list[str] = []
    specs_list: list[WikiPageSpec] = []

    for spec, children in specs_with_children:
        # Collect entities and deps for this page
        entities = []
        for rel_path in spec.files or []:
            file_info = file_analysis.files.get(rel_path)
            if file_info:
                for e in file_info.entities:
                    entities.append({**e, "file": rel_path})

        dep_info = summarize_page_deps(spec.files or [], dep_graph)
        dep_info_or_none = dep_info if any(dep_info.values()) else None
        entities_or_none = entities if entities else None

        # RAG retrieval
        queries = [f"{spec.title} {' '.join((spec.files or [])[:5])}"]
        if spec.purpose:
            queries.append(spec.purpose)
        if entities_or_none:
            entity_names = [e.get("name", "") for e in entities_or_none[:5] if e.get("name")]
            if entity_names:
                queries.append(" ".join(entity_names))

        query_vecs = []
        for q in queries:
            vec = await async_retry(
                embedding.embed, q,
                transient_exceptions=TRANSIENT_EXCEPTIONS,
                on_retry=on_retry,
            )
            query_vecs.append(vec)

        if len(query_vecs) > 1:
            context_chunks = store.multi_search(query_vecs, k=12)
        else:
            context_chunks = store.search(query_vecs[0], k=12)

        prompt = _build_page_prompt(
            spec, context_chunks, repo_name,
            dep_info_or_none, entities_or_none,
            child_contents=children,
        )
        prompts.append(prompt)
        specs_list.append(spec)

    system = _SYSTEM + get_language_instruction(wiki_language)
    responses = await llm.generate_batch(prompts, system=system)

    results: list[PageResult] = []
    for spec, content in zip(specs_list, responses):
        content = sanitize_mermaid_blocks(content)
        results.append(PageResult(slug=spec.slug, title=spec.title, content=content))

    return results
```

- [x] **Step 12: Run page generator tests**

Run: `uv run pytest tests/worker/test_page_generator.py -v`

Expected: All PASS.

- [x] **Step 13: Update `run_full_index` in `worker/jobs.py`**

Replace the flat loop (Stage 6 section, lines 535-582) with bottom-up generation:

```python
        # Stage 6: Bottom-up page generation
        logger.info("Stage 6: Page Generator starting (bottom-up)")
        from worker.pipeline.page_generator import (
            compute_generation_order,
            generate_page_batch,
        )

        levels = compute_generation_order(plan)
        generated: dict[str, PageResult] = {}
        page_order_counter = 0

        for depth_idx, level in enumerate(levels):
            specs_with_children: list[tuple[WikiPageSpec, list[PageResult] | None]] = []
            for page_spec in level:
                children = [
                    generated[p.slug]
                    for p in plan.pages
                    if p.parent == page_spec.title and p.slug in generated
                ]
                specs_with_children.append((page_spec, children or None))

            results = await generate_page_batch(
                specs_with_children,
                store,
                llm,
                embedding,
                repo_name=name,
                file_analysis=file_analysis,
                dep_graph=dep_graph,
                on_retry=_on_retry,
                wiki_language=wiki_language,
            )

            for result, (page_spec, _) in zip(results, specs_with_children):
                generated[result.slug] = result
                logger.info(
                    "Page generated: %s (%s), %d chars",
                    result.title,
                    result.slug,
                    len(result.content),
                )
                async with get_session(db_path) as s:
                    s.add(
                        WikiPage(
                            id=str(uuid.uuid4()),
                            repo_id=repo_id,
                            slug=result.slug,
                            title=result.title,
                            content=result.content,
                            page_order=page_order_counter,
                            parent_slug=page_spec.parent_slug,
                            description=page_spec.purpose,
                        )
                    )
                    await s.commit()
                await _write_text_async(wiki_dir / f"{result.slug}.md", result.content)
                page_order_counter += 1

            pages_done = sum(len(l) for l in levels[: depth_idx + 1])
            progress = 70 + int(27 * pages_done / total) if total > 0 else 97
            await _update_job(
                db_path,
                job_id,
                progress=progress,
                status_description=f"Generating pages (level {depth_idx + 1}/{len(levels)})...",
            )
```

Update the import at the top of `worker/jobs.py` (line 46) — replace:
```python
from worker.pipeline.page_generator import generate_page
```
with:
```python
from worker.pipeline.page_generator import (
    PageResult,
    compute_generation_order,
    generate_page,
    generate_page_batch,
)
```

Then remove the local imports of `compute_generation_order` and `generate_page_batch` from inside `run_full_index` and `run_refresh_index`.

- [x] **Step 14: Update `run_refresh_index` Stage 6**

Replace the flat loop in `run_refresh_index` (around lines 1050-1093) with bottom-up generation. This is similar to the full index but also needs to load preserved page content from disk for child pages:

```python
        # Stage 6: Bottom-up regeneration
        logger.info("Stage 6: Page Generator starting (bottom-up)")
        from worker.pipeline.page_generator import (
            PageResult as _PR,
            compute_generation_order,
            generate_page_batch,
        )

        wiki_dir = repo_data_dir / "wiki"
        wiki_dir.mkdir(exist_ok=True)

        # Load preserved pages from disk so they can serve as child content
        preserved_content: dict[str, _PR] = {}
        for p in old_plan.pages:
            if p.title not in affected_page_titles:
                md_path = wiki_dir / f"{p.slug}.md"
                if md_path.exists():
                    content = await asyncio.get_running_loop().run_in_executor(
                        None, md_path.read_text
                    )
                    preserved_content[p.slug] = _PR(
                        slug=p.slug, title=p.title, content=content
                    )

        levels = compute_generation_order(plan)
        generated: dict[str, _PR] = {}

        for depth_idx, level in enumerate(levels):
            specs_with_children: list[tuple[WikiPageSpec, list[_PR] | None]] = []
            for page_spec in level:
                children = []
                for p in plan.pages:
                    if p.parent == page_spec.title:
                        if p.slug in generated:
                            children.append(generated[p.slug])
                        elif p.slug in preserved_content:
                            children.append(preserved_content[p.slug])
                # Also check old_plan for preserved children
                for p in old_plan.pages:
                    if p.parent == page_spec.title and p.slug in preserved_content:
                        if not any(c.slug == p.slug for c in children):
                            children.append(preserved_content[p.slug])
                specs_with_children.append((page_spec, children or None))

            results = await generate_page_batch(
                specs_with_children,
                store,
                llm,
                embedding,
                repo_name=name,
                file_analysis=file_analysis,
                dep_graph=dep_graph,
                on_retry=_on_retry,
                wiki_language=wiki_language,
            )

            for result, (page_spec, _) in zip(results, specs_with_children):
                generated[result.slug] = result
                logger.info(
                    "Page updated: %s (%s), %d chars",
                    result.title,
                    result.slug,
                    len(result.content),
                )
                page_order = old_page_orders.get(result.slug, max_existing_order + 1 + len(generated))
                async with get_session(db_path) as s:
                    s.add(
                        WikiPage(
                            id=str(uuid.uuid4()),
                            repo_id=repo_id,
                            slug=result.slug,
                            title=result.title,
                            content=result.content,
                            page_order=page_order,
                            parent_slug=page_spec.parent_slug,
                            description=page_spec.purpose,
                        )
                    )
                    await s.commit()
                await _write_text_async(wiki_dir / f"{result.slug}.md", result.content)

            progress = 65 + int(30 * (depth_idx + 1) / len(levels)) if levels else 95
            await _update_job(
                db_path,
                job_id,
                progress=progress,
                status_description=f"Regenerating pages (level {depth_idx + 1}/{len(levels)})...",
            )
```

- [x] **Step 15: Run full test suite**

Run: `uv run pytest tests/ --ignore=tests/e2e -v`

Expected: All PASS. The integration tests (`test_full_pipeline_produces_pages`, `test_full_index_job_updates_status`, etc.) exercise `run_full_index` end-to-end and will validate the new bottom-up flow.

- [x] **Step 16: Lint and commit**

```bash
uv run ruff check worker/pipeline/page_generator.py worker/jobs.py tests/worker/test_page_generator.py
uv run ruff format worker/pipeline/page_generator.py worker/jobs.py tests/worker/test_page_generator.py
git add worker/pipeline/page_generator.py worker/jobs.py tests/worker/test_page_generator.py
git commit -m "feat: implement bottom-up multi-agent page generation with child content synthesis"
```

---

### Task 8: Final integration verification and cleanup

**Files:**
- All modified files from Tasks 1-7
- Test: `tests/test_integration.py`

- [x] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ --ignore=tests/e2e -v --tb=short`

Expected: All PASS.

- [x] **Step 2: Run linters**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: No errors.

- [x] **Step 3: Run frontend lint**

```bash
npm run lint --prefix web
```

Expected: No errors (no frontend files changed).

- [x] **Step 4: Verify no stale imports or dead code**

Check that the old `_WIKI_PLAN_SCHEMA` and `_build_prompt` are fully removed from `wiki_planner.py`. Check that `generate_page` import in `jobs.py` is still present (used by refresh path if needed, or remove if fully replaced by `generate_page_batch`).

- [x] **Step 5: Commit any cleanup**

```bash
git add -A
git commit -m "chore: final cleanup after wiki planner improvements"
```
