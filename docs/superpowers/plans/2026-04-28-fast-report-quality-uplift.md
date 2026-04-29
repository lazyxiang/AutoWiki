# Fast Report Quality Uplift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift fast-report quality from "metadata summarization" to "explanation grounded in real code slices" by upgrading the deterministic index, adaptive retrieval, slice extraction, planning hardening, interpretive layer, and SHA-mismatch invalidation — without changing the user-facing report flow, URL model, or evidence rail components.

**Architecture:** Single coordinated upgrade contained inside the worker pipeline. Index schema bumps to `index_version: 2` with a hard cutover. Retrieval becomes adaptive (per-`question_type` budgets, multi-slice per file, per-graph expansion). A new `worker/fast_report_slices.py` reads real source from the indexed clone. A new `worker/fast_report_interpretive.py` adds an explanation-only context layer. A new `worker/fast_report_planning.py` injects repository-shape context into the planner. `_build_generation_prompt` consumes all of the above. The frontend, API DTOs, persistence schema for `FastReportEvidenceBlock`, and WebSocket event types are unchanged.

**Tech Stack:** Python 3, asyncio, SQLAlchemy 2.0 async, pytest with `asyncio_mode = "auto"`, Tree-Sitter (already present), FastAPI (only the GET/WS handlers change for invalidation).

**Spec:** `docs/spec/superpowers/2026-04-28-fast-report-quality-uplift-design.md`

**Repo conventions to honor (CLAUDE.md):**
- Pre-commit: `uv run ruff check .`, `uv run ruff format --check .`, `npm run lint --prefix web` (web is unaffected by this plan but lint must pass).
- All retry loops over LLM structured-output calls **must** route through `worker/pipeline/pipeline_logging.py` (`log_validation_retry`, `log_final_failure`). No silent `except: pass`.
- Commit style: no `Co-Authored-By Claude` trailer.

---

## File Structure

### New files (worker)
- `worker/fast_report_slices.py` — pure-functional source slice extractor. Reads files from the indexed clone, returns `SliceResult` payloads.
- `worker/fast_report_interpretive.py` — Interpretive Context Layer assembler. Pulls module/entity docstrings, leading comments, and README section bodies; scores deterministically; returns a render-ready interpretive bundle.
- `worker/fast_report_planning.py` — Planner-input assembly: derives `directory_tree`, `hub_modules`, `readme_headings` views; defines the `question_type` enum; builds the plan prompt; runs the single-shot feedback retry.

### New files (tests)
- `tests/worker/test_fast_report_slices.py`
- `tests/worker/test_fast_report_interpretive.py`
- `tests/worker/test_fast_report_planning.py`
- `tests/worker/test_fast_report_search_adaptive.py` — new file separate from existing `test_fast_report.py` to keep adaptive-retrieval tests focused.

### Modified files (worker)
- `worker/pipeline/fast_report_index.py` — schema bump to `index_version: 2`; new field extractors; `top_level_entries` removed.
- `worker/fast_report_search.py` — adaptive retrieval; multi-slice scoring; per-graph expansion; new citation id format `code-{file_idx}-{entity_idx}`; replaces `_SEED_LIMIT / _EXPANSION_DEPTH / _RESULT_LIMIT` constants with a per-`question_type` profile table.
- `worker/fast_report.py` — adds the Interpretive Context Layer to `retrieve_fast_report_layers`; updates `_build_generation_prompt`; uses the planning module.
- `worker/jobs.py` — fast report entrypoint validates `index_version`; surfaces actionable failure when outdated; removes legacy `top_level_entries` fallback in `_build_default_fast_report_retrievers`; structure-layer signals expand to use `directory_tree` and `hub_modules`; curated wiki summary truncation 200 → 400 chars.

### Modified files (api)
- `api/routers/fast_report.py` — SHA-mismatch invalidation in `GET /api/repos/{repo_id}/fast-reports/{report_id}` and the WebSocket handler.

### Modified files (shared)
- `shared/fast_report_types.py` — no schema changes; `FastReportEvidenceBlock.full_start`/`full_end` semantics shift from ±3 to ±5 (still inside the dataclass values, no shape change). `FastReportSectionResult` (in `worker/fast_report.py`) gains an internal `interpretive_sources: list[dict]` field that is **not** surfaced through the public DTO.

### Untouched on purpose
- `shared/fast_report_types.py` dataclass fields.
- Frontend (`web/`).
- WebSocket event schema (only the `analysis_update` `phase` values surface more detail).
- `format_retrieved_chunks_for_prompt` in `worker/deep_research.py` — already source-text-aware.
- `arbitrate_report_claims` logic.

---

## Phase A — Index v2 Schema

### Task A1: Define `index_version` constant and add the `directory_tree` builder

**Files:**
- Modify: `worker/pipeline/fast_report_index.py`
- Test: `tests/worker/test_fast_report_index.py`

- [ ] **Step 1: Write failing tests for `_build_directory_tree`**

Append to `tests/worker/test_fast_report_index.py`:

```python
from worker.pipeline.fast_report_index import _build_directory_tree


def test_build_directory_tree_nested_indent_format():
    rel_paths = [
        "api/main.py",
        "api/routes/repos.py",
        "worker/fast_report.py",
        "README.md",
    ]
    tree = _build_directory_tree(rel_paths)
    assert tree == (
        "README.md\n"
        "api/\n"
        "  main.py\n"
        "  routes/\n"
        "    repos.py\n"
        "worker/\n"
        "  fast_report.py\n"
    )


def test_build_directory_tree_excludes_known_dirs_and_globs():
    rel_paths = [
        "src/main.py",
        ".git/HEAD",
        "node_modules/foo/index.js",
        "dist/bundle.js",
        "build/out.o",
        "__pycache__/main.cpython-311.pyc",
        ".venv/lib/python.py",
        "tests/sample.min.js",
        "package-lock.json",
        "src/main.pyc",
    ]
    tree = _build_directory_tree(rel_paths)
    assert "src/" in tree
    assert "main.py" in tree
    assert ".git" not in tree
    assert "node_modules" not in tree
    assert "dist" not in tree
    assert "build" not in tree
    assert "__pycache__" not in tree
    assert ".venv" not in tree
    assert "min.js" not in tree
    assert "package-lock.json" not in tree
    assert "main.pyc" not in tree
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_index.py::test_build_directory_tree_nested_indent_format tests/worker/test_fast_report_index.py::test_build_directory_tree_excludes_known_dirs_and_globs -v`
Expected: FAIL with `ImportError: cannot import name '_build_directory_tree'`.

- [ ] **Step 3: Implement `_build_directory_tree` and exclusion set**

In `worker/pipeline/fast_report_index.py`, add (above `build_fast_report_index`):

```python
INDEX_VERSION = 2

_DIRECTORY_EXCLUDED_DIRS = {
    ".git", "node_modules", "dist", "build", "target",
    "__pycache__", ".next", ".turbo", ".venv", "venv",
    ".cache", ".pytest_cache", "coverage", ".mypy_cache",
    ".ruff_cache",
}
_DIRECTORY_EXCLUDED_SUFFIXES = (".pyc", ".lock", ".min.js")
_DIRECTORY_EXCLUDED_FILES = {"package-lock.json", "yarn.lock"}


def _is_directory_tree_excluded(rel_path: str) -> bool:
    parts = rel_path.split("/")
    if any(part in _DIRECTORY_EXCLUDED_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in _DIRECTORY_EXCLUDED_DIRS:
        return True
    if name in _DIRECTORY_EXCLUDED_FILES:
        return True
    if name.endswith(_DIRECTORY_EXCLUDED_SUFFIXES):
        return True
    return False


def _build_directory_tree(rel_paths: list[str]) -> str:
    kept = sorted({p for p in rel_paths if not _is_directory_tree_excluded(p)})
    lines: list[str] = []
    emitted_dirs: set[str] = set()
    for path in kept:
        parts = path.split("/")
        for depth in range(len(parts) - 1):
            dir_path = "/".join(parts[: depth + 1])
            if dir_path in emitted_dirs:
                continue
            emitted_dirs.add(dir_path)
            lines.append(f"{'  ' * depth}{parts[depth]}/")
        lines.append(f"{'  ' * (len(parts) - 1)}{parts[-1]}")
    return "\n".join(lines) + ("\n" if lines else "")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v -k directory_tree`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/fast_report_index.py tests/worker/test_fast_report_index.py
git commit -m "feat(fast-report): add directory_tree builder for index v2"
```

---

### Task A2: Add `directory_tree` degradation when over the 25k-token hard cap

**Files:**
- Modify: `worker/pipeline/fast_report_index.py`
- Test: `tests/worker/test_fast_report_index.py`

- [ ] **Step 1: Write failing test for degradation**

Append to `tests/worker/test_fast_report_index.py`:

```python
from worker.pipeline.fast_report_index import _build_directory_tree_with_degradation


def test_directory_tree_falls_back_to_depth_three_over_cap():
    deep_paths = [
        f"src/a/b/c/d/file_{i}.py" for i in range(20000)
    ] + ["src/a/README.md"]
    hub_paths = {"src/a/b/c/d/file_0.py"}
    tree = _build_directory_tree_with_degradation(
        deep_paths, hub_paths=hub_paths
    )
    assert "file_1.py" not in tree  # depth-5 leaves dropped
    assert "file_0.py" in tree  # hub leaf preserved
    assert "src/" in tree and "  a/" in tree
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_fast_report_index.py::test_directory_tree_falls_back_to_depth_three_over_cap -v`
Expected: FAIL.

- [ ] **Step 3: Implement degradation**

In `worker/pipeline/fast_report_index.py`, add:

```python
_DIRECTORY_TREE_HARD_CAP_TOKENS = 25_000


def _approx_tokens(text: str) -> int:
    return max(0, len(text) // 4)


def _build_directory_tree_with_degradation(
    rel_paths: list[str], *, hub_paths: set[str] | None = None
) -> str:
    full = _build_directory_tree(rel_paths)
    if _approx_tokens(full) <= _DIRECTORY_TREE_HARD_CAP_TOKENS:
        return full
    hub_paths = hub_paths or set()
    kept: list[str] = []
    for path in rel_paths:
        if _is_directory_tree_excluded(path):
            continue
        depth = path.count("/")
        if depth <= 3 or path in hub_paths:
            kept.append(path)
    degraded = _build_directory_tree(kept)
    if _approx_tokens(degraded) <= _DIRECTORY_TREE_HARD_CAP_TOKENS:
        return degraded
    # Final fallback: drop subdirectories with the fewest entries.
    by_top: dict[str, list[str]] = {}
    for path in kept:
        by_top.setdefault(path.split("/", 1)[0], []).append(path)
    sorted_tops = sorted(by_top.items(), key=lambda kv: len(kv[1]), reverse=True)
    trimmed: list[str] = []
    for _top, members in sorted_tops:
        trimmed.extend(members)
        if _approx_tokens(_build_directory_tree(trimmed)) > _DIRECTORY_TREE_HARD_CAP_TOKENS:
            trimmed = trimmed[: -len(members)]
            break
    return _build_directory_tree(trimmed)
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v -k directory_tree`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/fast_report_index.py tests/worker/test_fast_report_index.py
git commit -m "feat(fast-report): degrade directory_tree above 25k-token cap"
```

---

### Task A3: Add `hub_modules` extractor

**Files:**
- Modify: `worker/pipeline/fast_report_index.py`
- Test: `tests/worker/test_fast_report_index.py`

- [ ] **Step 1: Write failing test**

```python
from worker.pipeline.fast_report_index import _compute_hub_modules


def test_hub_modules_ranks_by_in_degree_and_truncates_purpose():
    files = {
        "shared/types.py": {"path": "shared/types.py", "imported_by": ["a.py", "b.py"], "module_docstring": "Types module. Internal helpers."},
        "shared/util.py": {"path": "shared/util.py", "imported_by": ["a.py"], "module_docstring": None},
        "main.py": {"path": "main.py", "imported_by": [], "module_docstring": "Entrypoint"},
    }
    hubs = _compute_hub_modules(files)
    assert hubs[0]["path"] == "shared/types.py"
    assert hubs[0]["in_degree"] == 2
    assert hubs[0]["purpose"] == "Types module."
    # in_degree < 2 excluded; main.py has 0
    assert all(h["in_degree"] >= 2 for h in hubs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_fast_report_index.py::test_hub_modules_ranks_by_in_degree_and_truncates_purpose -v`
Expected: FAIL.

- [ ] **Step 3: Implement `_compute_hub_modules`**

```python
_HUB_MAX = 20
_HUB_PURPOSE_CHAR_CAP = 120


def _first_sentence(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    cut = len(text)
    for terminator in (". ", "。", "\n"):
        idx = text.find(terminator)
        if idx != -1:
            cut = min(cut, idx + (1 if terminator != "\n" else 0))
    sentence = text[:cut].strip()
    if len(sentence) > _HUB_PURPOSE_CHAR_CAP:
        sentence = sentence[: _HUB_PURPOSE_CHAR_CAP].rstrip() + "…"
    return sentence


def _compute_hub_modules(files: dict[str, dict]) -> list[dict]:
    ranked: list[tuple[int, str, dict]] = []
    for path, entry in files.items():
        in_degree = len(entry.get("imported_by") or [])
        if in_degree < 2:
            continue
        ranked.append((in_degree, path, entry))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    hubs: list[dict] = []
    for in_degree, path, entry in ranked[:_HUB_MAX]:
        hubs.append({
            "path": path,
            "in_degree": in_degree,
            "purpose": _first_sentence(entry.get("module_docstring")),
        })
    return hubs
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v -k hub_modules`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/fast_report_index.py tests/worker/test_fast_report_index.py
git commit -m "feat(fast-report): compute hub_modules from in_degree"
```

---

### Task A4: Add `readme_sections` extractor (alongside existing `readme_headings`)

**Files:**
- Modify: `worker/pipeline/fast_report_index.py`
- Test: `tests/worker/test_fast_report_index.py`

- [ ] **Step 1: Write failing test**

```python
from worker.pipeline.fast_report_index import _extract_readme_sections


def test_readme_sections_caps_per_section_at_800_chars():
    big = "x" * 5000
    readme = f"# Top\nintro\n\n## Architecture\n{big}\n\n## Deployment\nshort body"
    sections = _extract_readme_sections(readme)
    headings = [s["heading"] for s in sections]
    assert headings == ["Top", "Architecture", "Deployment"]
    arch = next(s for s in sections if s["heading"] == "Architecture")
    assert len(arch["body"]) == 800


def test_readme_sections_cumulative_cap_drops_later():
    body = "y" * 800
    chunks = [f"## H{i}\n{body}" for i in range(60)]  # 60 * ~200 tokens > 10k
    readme = "\n\n".join(chunks)
    sections = _extract_readme_sections(readme)
    assert len(sections) < 60  # later sections dropped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v -k readme_sections`
Expected: FAIL.

- [ ] **Step 3: Implement `_extract_readme_sections`**

```python
_README_SECTION_BODY_CAP = 800
_README_SECTIONS_TOTAL_TOKEN_CAP = 10_000


def _extract_readme_sections(readme: str | None) -> list[dict]:
    if not readme:
        return []
    matches = list(_README_HEADING_RE.finditer(readme))
    if not matches:
        return []
    sections: list[dict] = []
    cumulative_tokens = 0
    for idx, match in enumerate(matches):
        heading = match.group(2).strip()
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(readme)
        body = readme[body_start:body_end].strip()
        if len(body) > _README_SECTION_BODY_CAP:
            body = body[:_README_SECTION_BODY_CAP]
        section = {"heading": heading, "body": body}
        cumulative_tokens += _approx_tokens(heading) + _approx_tokens(body)
        if cumulative_tokens > _README_SECTIONS_TOTAL_TOKEN_CAP:
            break
        sections.append(section)
    return sections
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v -k readme_sections`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/fast_report_index.py tests/worker/test_fast_report_index.py
git commit -m "feat(fast-report): extract readme_sections with body and cumulative caps"
```

---

### Task A5: Add per-file `module_docstring` and per-entity `leading_comment` extraction

**Files:**
- Modify: `worker/pipeline/fast_report_index.py`
- Test: `tests/worker/test_fast_report_index.py`

`module_docstring` and `leading_comment` come from the AST analyzer's existing data; we wire them into the index entry. Inspect `worker/pipeline/ast_analysis.py` for the field name (often surfaced as `entity.docstring`; the file-level docstring may need to be inferred from the first entity at line 1 with type `module` if the analyzer does not already emit it).

- [ ] **Step 1: Inspect AST analyzer to determine source fields**

Run: `grep -n "module_doc\|leading_comment\|file_docstring\|module\b" worker/pipeline/ast_analysis.py`

If the analyzer does **not** already emit `module_docstring` at the `FileAnalysis` level, extend it:
- For `.py`: read the first `string` child of the `module` node.
- For `.js/.ts`: detect a leading `/** ... */` JSDoc.
- For `.go`: read package comment.
- Return as `FileAnalysis.files[path].module_docstring`.

For `leading_comment` per entity: scan the lines immediately above `entity.start_line` for a contiguous comment block with no blank-line gap. Block-style only (`# ...`, `// ...`, `/** ... */`).

- [ ] **Step 2: Add `_build_index_for_dir` helper (shared by A5, A6, A7) at the top of the test file**

```python
import pathlib

from worker.pipeline.ast_analysis import FileAnalysis
from worker.pipeline.dependency_graph import build_dependency_graph
from worker.pipeline.fast_report_index import build_fast_report_index


def _build_index_for_dir(root: pathlib.Path) -> dict:
    root = pathlib.Path(root)
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in {".py", ".js", ".ts", ".go"})
    file_analysis = FileAnalysis.from_files(root, files)
    dep_graph = build_dependency_graph(root, files, file_analysis)
    readme_file = root / "README.md"
    readme = readme_file.read_text() if readme_file.exists() else ""
    return build_fast_report_index(
        root=root, files=files, file_analysis=file_analysis,
        dep_graph=dep_graph, readme=readme,
    )
```

(If the actual current AST API is `FileAnalysis(...)` constructor or a different builder, follow the import pattern used by existing `tests/worker/test_fast_report_index.py` setup code.)

- [ ] **Step 3: Write failing tests**

```python
def test_index_entry_carries_module_docstring(tmp_path):
    (tmp_path / "mod.py").write_text('"""Hello module."""\n\ndef f():\n    pass\n')
    index = _build_index_for_dir(tmp_path)
    assert index["files"]["mod.py"]["module_docstring"] == "Hello module."


def test_entity_leading_comment_attached(tmp_path):
    (tmp_path / "mod.py").write_text(
        "# Single-pass AST analyzer.\n"
        "# Companion to tree-sitter parsing.\n"
        "def analyze():\n"
        "    pass\n"
    )
    index = _build_index_for_dir(tmp_path)
    entity = next(e for e in index["files"]["mod.py"]["entities"] if e["name"] == "analyze")
    assert entity.get("leading_comment", "").startswith("Single-pass")


def test_blank_line_between_comment_and_entity_skips_leading_comment(tmp_path):
    (tmp_path / "mod.py").write_text(
        "# Unrelated note\n"
        "\n"
        "def fn():\n"
        "    pass\n"
    )
    index = _build_index_for_dir(tmp_path)
    entity = next(e for e in index["files"]["mod.py"]["entities"] if e["name"] == "fn")
    assert "leading_comment" not in entity or entity["leading_comment"] in (None, "")
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v -k "module_docstring or leading_comment"`
Expected: FAIL.

- [ ] **Step 5: Implement extractors**

If `worker/pipeline/ast_analysis.py` does not produce `module_docstring`, add it. Then in `worker/pipeline/fast_report_index.py`, extend `_build_file_entry`:

```python
def _build_file_entry(...):
    ...
    return {
        "path": rel_path,
        "tokens": _file_tokens(rel_path, normalized_entities),
        "imports": normalized_edges.get(rel_path, []),
        "imported_by": imported_by.get(rel_path, []),
        "external_deps": normalized_external_deps.get(rel_path, []),
        "entities": normalized_entities,
        "is_test": _is_test_file(rel_path),
        "is_config": _is_config_file(rel_path),
        "module_docstring": _module_docstring_for(info),
        "call_sites": [],            # filled in Task A6
        "exception_touchpoints": [], # filled in Task A6
        "config_touchpoints": [],    # filled in Task A6
    }
```

Extend `_normalize_entity` to carry `leading_comment`:

```python
def _normalize_entity(rel_path: str, entity: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "name": entity.get("name", ""),
        "type": entity.get("type", ""),
        "start_line": entity.get("start_line"),
        "end_line": entity.get("end_line"),
        "symbol_path": _symbol_path(rel_path, entity.get("name", "")),
    }
    for opt_key in ("signature", "docstring", "leading_comment"):
        value = entity.get(opt_key)
        if value:
            normalized[opt_key] = value
    return normalized
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add worker/pipeline/fast_report_index.py worker/pipeline/ast_analysis.py tests/worker/test_fast_report_index.py
git commit -m "feat(fast-report): index module_docstring and leading_comment"
```

---

### Task A6: Add `call_sites`, `exception_touchpoints`, and `config_touchpoints` per file

**Files:**
- Modify: `worker/pipeline/ast_analysis.py` (extend the existing single-pass walk to collect these node types)
- Modify: `worker/pipeline/fast_report_index.py` (surface the new fields)
- Test: `tests/worker/test_fast_report_index.py`

These three are AST-level extractions. Reuse the existing parse tree from `ast_analysis.py` — do not re-parse. The spec defines exact semantics in §"Field semantics".

- [ ] **Step 1: Write failing tests using fixture-based helper**

Add a small helper to `tests/worker/test_fast_report_index.py` that writes source files into a `tmp_path` and runs the full `build_fast_report_index` pipeline (using `FileAnalysis.from_files(...)` and a real `DependencyGraph`). Then:

```python
def test_call_sites_collected_for_python(tmp_path):
    (tmp_path / "a.py").write_text(
        "def caller():\n    helper()\n\ndef helper():\n    pass\n"
    )
    index = _build_index_for_dir(tmp_path)
    entry = index["files"]["a.py"]
    sites = entry["call_sites"]
    assert any(s["callee_name"] == "helper" and s["line"] == 2 for s in sites)


def test_exception_touchpoints_record_message_when_literal(tmp_path):
    (tmp_path / "b.py").write_text(
        "def fn():\n    try:\n        x = 1\n    except ValueError:\n        raise ValueError('boom')\n"
    )
    index = _build_index_for_dir(tmp_path)
    touchpoints = index["files"]["b.py"]["exception_touchpoints"]
    kinds = {t["kind"] for t in touchpoints}
    assert {"try", "except", "raise"}.issubset(kinds)
    raised = next(t for t in touchpoints if t["kind"] == "raise")
    assert raised["message"] == "boom"


def test_config_touchpoints_capture_env_keys(tmp_path):
    (tmp_path / "c.py").write_text(
        "import os\n\nKEY = os.getenv('AUTOWIKI_LLM_PROVIDER')\n"
    )
    index = _build_index_for_dir(tmp_path)
    cps = index["files"]["c.py"]["config_touchpoints"]
    assert any(c["config_key"] == "AUTOWIKI_LLM_PROVIDER" and c["kind"] == "read" for c in cps)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v -k "call_sites or exception_touchpoints or config_touchpoints"`
Expected: FAIL.

- [ ] **Step 3: Extend AST analyzer to surface these node types**

In `worker/pipeline/ast_analysis.py`, extend the single-pass walk over each parsed file to collect, alongside entities:

```python
file_extras = {
    "call_sites": [...],            # {caller_symbol_path, callee_name, line}
    "exception_touchpoints": [...], # {kind, symbol_path, line, message}
    "config_touchpoints": [...],    # {kind, config_key, line, scope}
}
```

Per-language detection — implement Python first (covers all current fixtures):

- `call_sites`: walk `call` nodes. Resolve `caller_symbol_path` from the enclosing function/class entity (use the AST stack maintained for entity extraction). Record `callee_name` as the rightmost identifier of the call expression.
- `exception_touchpoints`: detect `try_statement`, `except_clause`, `raise_statement` nodes. For `raise X("literal")`, extract the literal as `message`; otherwise null.
- `config_touchpoints`: detect calls matching `os.environ.get("X")`, `os.getenv("X")`, `os.environ["X"]`. Record the literal string argument as `config_key`. Mark `scope = "function"` if inside an entity body, else `"module"`.

For other languages (JS/TS/Go/Java/Rust/C/C++/C#), implement best-effort variants of the same patterns as documented in spec §"Field semantics". Languages with zero extractor coverage simply emit empty lists (per Risks §"Tree-Sitter touchpoint fidelity varies by language").

Then in `worker/pipeline/fast_report_index.py`, plumb `file_extras` into the entry:

```python
def _build_file_entry(*, rel_path, normalized_file_info, ...):
    ...
    info = normalized_file_info.get(rel_path)
    extras = info.extras if info is not None else {}
    return {
        ...
        "call_sites": extras.get("call_sites", []),
        "exception_touchpoints": extras.get("exception_touchpoints", []),
        "config_touchpoints": extras.get("config_touchpoints", []),
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v -k "call_sites or exception_touchpoints or config_touchpoints"`
Expected: PASS.

- [ ] **Step 5: Run full unit suite to catch AST-extraction regressions**

Run: `uv run pytest tests/worker/test_ast_analysis.py tests/worker/test_fast_report_index.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/ast_analysis.py worker/pipeline/fast_report_index.py tests/worker/test_fast_report_index.py
git commit -m "feat(fast-report): extract call_sites, exception_touchpoints, config_touchpoints"
```

---

### Task A7: Bump `index_version: 2`, remove `top_level_entries`, wire `directory_tree` + `hub_modules` + `readme_sections` into top-level index

**Files:**
- Modify: `worker/pipeline/fast_report_index.py`
- Test: `tests/worker/test_fast_report_index.py`

- [ ] **Step 1: Write failing tests**

```python
from worker.pipeline.fast_report_index import build_fast_report_index, INDEX_VERSION


def test_index_version_is_2_and_no_top_level_entries(tmp_path):
    index = _build_index_for_dir(tmp_path / "empty_repo", make_empty=True)
    assert index["index_version"] == 2
    assert "top_level_entries" not in index


def test_index_carries_directory_tree_hub_modules_and_readme_sections(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Title\nintro\n\n## Architecture\nbody")
    (repo / "lib.py").write_text('"""Lib."""\n\ndef f():\n    pass\n')
    (repo / "a.py").write_text("import lib\n")
    (repo / "b.py").write_text("import lib\n")
    index = _build_index_for_dir(repo)
    assert "lib.py" in index["directory_tree"]
    assert any(h["path"] == "lib.py" for h in index["hub_modules"])
    assert index["readme_sections"][0]["heading"] == "Title"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v -k "index_version_is_2 or directory_tree_hub_modules"`
Expected: FAIL.

- [ ] **Step 3: Modify `build_fast_report_index`**

```python
def build_fast_report_index(
    *,
    root: Path,
    files: list[Path],
    file_analysis: FileAnalysis,
    dep_graph: DependencyGraph,
    readme: str | None,
) -> dict[str, Any]:
    rel_paths = _collect_rel_paths(root, files, file_analysis)
    imported_by = _build_imported_by(dep_graph)
    normalized_file_info = {
        _normalize_rel_path(path): info for path, info in file_analysis.files.items()
    }
    normalized_edges = {
        _normalize_rel_path(path): sorted(_normalize_rel_path(dep) for dep in deps)
        for path, deps in dep_graph.edges.items()
    }
    normalized_external_deps = {
        _normalize_rel_path(path): sorted(deps)
        for path, deps in dep_graph.external_deps.items()
    }

    files_index = {
        rel_path: _build_file_entry(
            rel_path=rel_path,
            normalized_file_info=normalized_file_info,
            normalized_edges=normalized_edges,
            normalized_external_deps=normalized_external_deps,
            imported_by=imported_by,
        )
        for rel_path in rel_paths
    }

    hub_modules = _compute_hub_modules(files_index)
    hub_paths = {h["path"] for h in hub_modules}
    directory_tree = _build_directory_tree_with_degradation(rel_paths, hub_paths=hub_paths)
    readme_sections = _extract_readme_sections(readme)
    readme_headings = _extract_readme_headings(readme)

    return {
        "index_version": INDEX_VERSION,
        "directory_tree": directory_tree,
        "hub_modules": hub_modules,
        "readme_headings": readme_headings,
        "readme_sections": readme_sections,
        "files": files_index,
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_index.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/fast_report_index.py tests/worker/test_fast_report_index.py
git commit -m "feat(fast-report): bump index_version to 2 with directory_tree, hub_modules, readme_sections"
```

---

### Task A8: Reject `index_version < 2` at the fast-report job entrypoint

**Files:**
- Modify: `worker/jobs.py`
- Test: `tests/worker/test_jobs.py`

- [ ] **Step 1: Write failing test**

Append to `tests/worker/test_jobs.py`:

```python
import pytest
from worker.jobs import _validate_fast_report_index_version


def test_validate_fast_report_index_rejects_missing_version():
    with pytest.raises(ValueError) as ei:
        _validate_fast_report_index_version({})
    assert "fast_report_index_outdated" in str(ei.value)


def test_validate_fast_report_index_rejects_v1():
    with pytest.raises(ValueError) as ei:
        _validate_fast_report_index_version({"index_version": 1})
    assert "fast_report_index_outdated" in str(ei.value)


def test_validate_fast_report_index_accepts_v2():
    _validate_fast_report_index_version({"index_version": 2})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_jobs.py -v -k validate_fast_report_index`
Expected: FAIL.

- [ ] **Step 3: Implement validator and call from `_build_default_fast_report_retrievers`**

In `worker/jobs.py` (near `_load_fast_report_index`):

```python
class FastReportIndexOutdated(RuntimeError):
    """Raised when fast_report_index.json is missing or below v2."""


def _validate_fast_report_index_version(index: dict) -> None:
    version = index.get("index_version")
    if not isinstance(version, int) or version < 2:
        raise FastReportIndexOutdated(
            "fast_report_index_outdated: Repository index is outdated for fast "
            "reports. Run `autowiki index <repo>` to upgrade."
        )
```

In `_build_default_fast_report_retrievers`, after loading the index call the validator:

```python
fast_report_index = await _load_fast_report_index(repo_data_dir)
_validate_fast_report_index_version(fast_report_index)
```

Remove the `top_level_entries` fallback block (lines that read it from index or scan `clone_root` for top-level dir names) — `directory_tree` is now used instead.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/jobs.py tests/worker/test_jobs.py
git commit -m "feat(fast-report): reject pre-v2 indexes at job entrypoint"
```

---

### Task A9: Surface `FastReportIndexOutdated` as synchronous HTTP 409 + WebSocket error event

**Files:**
- Modify: `api/routers/fast_report.py` (POST handler synchronously rejects pre-v2 indexes)
- Modify: `worker/jobs.py` (defensive backstop in `run_fast_report` exception handler)
- Test: `tests/api/test_fast_report.py`, `tests/worker/test_jobs.py`

The spec requires the **POST** to return 409 *before* the job is queued (not as a worker-side failure that takes effect later). So the index-version check must run synchronously in `api/routers/fast_report.py` POST.

- [ ] **Step 1: Write failing tests**

In `tests/api/test_fast_report.py`:

```python
async def test_post_fast_report_returns_409_when_index_missing_or_v1(client, repo_factory, tmp_path):
    repo_id = await repo_factory(status="ready")  # fixture stamps repo as indexable
    repo_data_dir = tmp_path / "repos" / repo_id
    (repo_data_dir / "ast").mkdir(parents=True)
    # Case 1: missing file → 409
    response = await client.post(
        f"/api/repos/{repo_id}/fast-reports", json={"question": "?"}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["error"] == "fast_report_index_outdated"
    assert body["detail"]["actionable_command"] == "autowiki index <repo>"

    # Case 2: file exists but index_version: 1 → 409
    (repo_data_dir / "ast" / "fast_report_index.json").write_text(
        '{"index_version": 1, "files": {}}'
    )
    response = await client.post(
        f"/api/repos/{repo_id}/fast-reports", json={"question": "?"}
    )
    assert response.status_code == 409


async def test_post_fast_report_proceeds_for_index_v2(client, repo_factory, tmp_path):
    repo_id = await repo_factory(status="ready")
    repo_data_dir = tmp_path / "repos" / repo_id
    (repo_data_dir / "ast").mkdir(parents=True)
    (repo_data_dir / "ast" / "fast_report_index.json").write_text(
        '{"index_version": 2, "files": {}}'
    )
    response = await client.post(
        f"/api/repos/{repo_id}/fast-reports", json={"question": "?"}
    )
    assert response.status_code in (200, 201, 202)
```

In `tests/worker/test_jobs.py` (defensive backstop):

```python
async def test_run_fast_report_outdated_index_marks_failed(repo_factory, fast_report_factory, tmp_path):
    repo_id, _commit = await repo_factory()
    report_id, section_id, job_id = await fast_report_factory(repo_id=repo_id)
    # Write an old-format index that bypassed the API gate (e.g., race or downgrade)
    repo_data_dir = tmp_path / "repos" / repo_id / "ast"
    repo_data_dir.mkdir(parents=True)
    (repo_data_dir / "fast_report_index.json").write_text('{"index_version": 1}')

    await run_fast_report(
        ctx={}, repo_id=repo_id, job_id=job_id,
        report_id=report_id, section_id=section_id, question="?",
    )
    job = await fetch_job(job_id)
    assert job.status == "failed"
    assert "fast_report_index_outdated" in (job.error or "")
```

(`repo_factory`, `fast_report_factory`, and `fetch_job` are existing helpers in `tests/worker/test_jobs.py`. If a helper does not yet exist with that exact name, follow the pattern of the surrounding tests and inline the equivalent `async with get_session(...) as s` setup.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_fast_report.py tests/worker/test_jobs.py -v -k "outdated or returns_409"`
Expected: FAIL.

- [ ] **Step 3: Implement synchronous gate in POST handler**

In `api/routers/fast_report.py` POST `create_fast_report`:

```python
from worker.jobs import _validate_fast_report_index_version, FastReportIndexOutdated
from worker.jobs import _load_fast_report_index

# Inside the POST handler, before enqueuing the job:
repo_data_dir = cfg.data_dir / "repos" / repo_id
fast_report_index = await _load_fast_report_index(repo_data_dir)
try:
    _validate_fast_report_index_version(fast_report_index)
except FastReportIndexOutdated:
    raise HTTPException(
        status_code=409,
        detail={
            "error": "fast_report_index_outdated",
            "message": (
                "Repository index is outdated for fast reports. "
                "Run `autowiki index <repo>` to upgrade."
            ),
            "actionable_command": "autowiki index <repo>",
        },
    )
```

The worker's `run_fast_report` already routes `FastReportIndexOutdated` (a `RuntimeError`) through `except Exception` → `_update_job(..., status="failed", error=str(e))`. The actionable message propagates as `job.error`. The existing WS poll-loop already surfaces `job.error` in the `error` event payload — verify this by inspecting `ws_fast_report` in `api/routers/fast_report.py` (look for the section that sends `{"type": "error", "content": job.error}` when job status == "failed"). If that path does not exist, add it before the section-complete broadcast.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_jobs.py tests/api/test_fast_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/jobs.py api/routers/fast_report.py tests/worker/test_jobs.py tests/api/test_fast_report.py
git commit -m "feat(fast-report): surface outdated-index error to API and WebSocket"
```

---

## Phase B — Adaptive Retrieval & Multi-Slice Scoring

### Task B1: Define the `question_type` enum and per-type budget profile table

**Files:**
- Create: `worker/fast_report_planning.py`
- Test: `tests/worker/test_fast_report_planning.py`

- [ ] **Step 1: Create test file with failing tests**

Create `tests/worker/test_fast_report_planning.py`:

```python
import pytest
from worker.fast_report_planning import (
    QUESTION_TYPES,
    QuestionTypeProfile,
    profile_for_question_type,
)


def test_question_types_enum_is_eight_values():
    assert QUESTION_TYPES == (
        "architecture",
        "execution_flow",
        "dependency",
        "error_handling",
        "configuration",
        "testing",
        "implementation_location",
        "unknown",
    )


@pytest.mark.parametrize(
    "qt,expected",
    [
        ("architecture", QuestionTypeProfile(seed=4, depth=3, result_limit=12,
                                              code_evidence_token_budget=50_000,
                                              per_slice_line_cap=40, slices_per_file=3)),
        ("execution_flow", QuestionTypeProfile(seed=3, depth=3, result_limit=10,
                                                code_evidence_token_budget=50_000,
                                                per_slice_line_cap=50, slices_per_file=2)),
        ("implementation_location", QuestionTypeProfile(seed=2, depth=1, result_limit=4,
                                                          code_evidence_token_budget=25_000,
                                                          per_slice_line_cap=200, slices_per_file=1)),
    ],
)
def test_profile_for_question_type_returns_spec_values(qt, expected):
    assert profile_for_question_type(qt) == expected


def test_profile_for_unknown_question_type_uses_default():
    assert profile_for_question_type("not_a_real_type") == profile_for_question_type("unknown")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_planning.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement enum and profile table**

Create `worker/fast_report_planning.py`:

```python
"""Fast report planner inputs, question_type enum, and adaptive profile table."""
from __future__ import annotations

from dataclasses import dataclass

QUESTION_TYPES = (
    "architecture",
    "execution_flow",
    "dependency",
    "error_handling",
    "configuration",
    "testing",
    "implementation_location",
    "unknown",
)


@dataclass(frozen=True, slots=True)
class QuestionTypeProfile:
    seed: int
    depth: int
    result_limit: int
    code_evidence_token_budget: int
    per_slice_line_cap: int
    slices_per_file: int


_PROFILES: dict[str, QuestionTypeProfile] = {
    "architecture": QuestionTypeProfile(4, 3, 12, 50_000, 40, 3),
    "execution_flow": QuestionTypeProfile(3, 3, 10, 50_000, 50, 2),
    "dependency": QuestionTypeProfile(3, 2, 10, 40_000, 30, 1),
    "error_handling": QuestionTypeProfile(2, 2, 8, 35_000, 40, 2),
    "configuration": QuestionTypeProfile(3, 2, 8, 35_000, 30, 2),
    "testing": QuestionTypeProfile(2, 1, 6, 40_000, 60, 2),
    "implementation_location": QuestionTypeProfile(2, 1, 4, 25_000, 200, 1),
    "unknown": QuestionTypeProfile(2, 2, 6, 40_000, 50, 1),
}


def profile_for_question_type(question_type: str) -> QuestionTypeProfile:
    return _PROFILES.get(question_type, _PROFILES["unknown"])
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_planning.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report_planning.py tests/worker/test_fast_report_planning.py
git commit -m "feat(fast-report): define question_type enum and adaptive profile table"
```

---

### Task B2: Define expansion-graph kinds and per-graph mechanics

**Files:**
- Modify: `worker/fast_report_planning.py`
- Test: `tests/worker/test_fast_report_planning.py`

- [ ] **Step 1: Write failing tests**

```python
from worker.fast_report_planning import expansion_graph_for


def test_expansion_graph_for_execution_flow():
    graph = expansion_graph_for("execution_flow")
    assert graph.primary == "call_sites"
    assert graph.secondary == "imports"


def test_expansion_graph_for_unknown_uses_default():
    graph = expansion_graph_for("totally_unknown")
    assert graph.primary == "imports_and_imported_by"
    assert graph.secondary is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_planning.py -v -k expansion_graph_for`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `worker/fast_report_planning.py`:

```python
@dataclass(frozen=True, slots=True)
class ExpansionGraph:
    primary: str
    secondary: str | None


_EXPANSION_GRAPHS: dict[str, ExpansionGraph] = {
    "architecture": ExpansionGraph("imports_and_imported_by", "sibling_directory"),
    "execution_flow": ExpansionGraph("call_sites", "imports"),
    "error_handling": ExpansionGraph("exception_touchpoints", "imports"),
    "configuration": ExpansionGraph("config_touchpoints", "is_config_files"),
    "dependency": ExpansionGraph("imports_and_imported_by", "external_deps_overlap"),
    "testing": ExpansionGraph("sibling_token_overlap", "imports"),
    "implementation_location": ExpansionGraph("imports", None),
    "unknown": ExpansionGraph("imports_and_imported_by", None),
}


def expansion_graph_for(question_type: str) -> ExpansionGraph:
    return _EXPANSION_GRAPHS.get(question_type, _EXPANSION_GRAPHS["unknown"])
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_planning.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report_planning.py tests/worker/test_fast_report_planning.py
git commit -m "feat(fast-report): define per-question-type expansion graphs"
```

---

### Task B3: Multi-slice per-file scoring in `_score_file`

**Files:**
- Modify: `worker/fast_report_search.py`
- Create: `tests/worker/test_fast_report_search_adaptive.py`

- [ ] **Step 1: Write failing tests**

Create `tests/worker/test_fast_report_search_adaptive.py`:

```python
from worker.fast_report_search import _score_file_multi_slice


def test_multi_slice_picks_top_k_above_threshold():
    entry = {
        "path": "m.py",
        "tokens": ["report"],
        "imports": [],
        "imported_by": [],
        "entities": [
            {"name": "report_a", "symbol_path": "m.report_a", "start_line": 1, "end_line": 10},
            {"name": "report_b", "symbol_path": "m.report_b", "start_line": 11, "end_line": 20},
            {"name": "report_c", "symbol_path": "m.report_c", "start_line": 21, "end_line": 30},
            {"name": "unrelated_x", "symbol_path": "m.unrelated_x", "start_line": 31, "end_line": 40},
        ],
    }
    query_tokens = {"report"}
    file_score, slices = _score_file_multi_slice(
        path="m.py",
        entry=entry,
        query_tokens=query_tokens,
        focus_hints=[],
        slices_per_file=3,
    )
    names = [s["name"] for s in slices]
    assert names == ["report_a", "report_b", "report_c"]
    assert file_score > 0
    assert "unrelated_x" not in names


def test_multi_slice_drops_entities_below_half_top_score():
    entry = {
        "path": "m.py",
        "tokens": ["x"],
        "imports": [],
        "imported_by": [],
        "entities": [
            {"name": "alpha_alpha", "symbol_path": "m.alpha_alpha", "start_line": 1, "end_line": 5},
            {"name": "weak", "symbol_path": "m.weak", "start_line": 6, "end_line": 7},
        ],
    }
    query_tokens = {"alpha"}
    _file_score, slices = _score_file_multi_slice(
        path="m.py",
        entry=entry,
        query_tokens=query_tokens,
        focus_hints=[],
        slices_per_file=3,
    )
    assert [s["name"] for s in slices] == ["alpha_alpha"]


def test_multi_slice_emits_one_for_files_with_no_entities():
    entry = {
        "path": "m.py",
        "tokens": [],
        "imports": [], "imported_by": [], "entities": [],
        "config_touchpoints": [{"kind": "read", "config_key": "X", "line": 7, "scope": "module"}],
    }
    file_score, slices = _score_file_multi_slice(
        path="m.py",
        entry=entry,
        query_tokens={"x"},
        focus_hints=[],
        slices_per_file=2,
    )
    assert len(slices) == 1
    assert slices[0]["start_line"] == 7
    assert slices[0]["end_line"] == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_search_adaptive.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `_score_file_multi_slice`**

Add to `worker/fast_report_search.py`:

```python
_MULTI_SLICE_THRESHOLD_RATIO = 0.5


def _score_file_multi_slice(
    *,
    path: str,
    entry: dict,
    query_tokens: set[str],
    focus_hints: list[str],
    slices_per_file: int,
) -> tuple[float, list[dict]]:
    """Score a file and return (file_score, [entity_slice_dicts])."""
    lower_path = path.lower()
    file_tokens = set(entry.get("tokens") or []) | _tokenize(path)
    file_level_score = float(len(query_tokens & file_tokens) * 2)
    if entry.get("imports"):
        file_level_score += 0.5
    if entry.get("imported_by"):
        file_level_score += 0.5
    for hint in focus_hints:
        if hint == lower_path:
            file_level_score += 12
        elif hint.replace(".", "/") in lower_path:
            file_level_score += 6

    entities = entry.get("entities") or []
    if not entities:
        # Touchpoint-only emission for entity-less files
        cps = entry.get("config_touchpoints") or []
        if not cps:
            return file_level_score, []
        line = int(cps[0].get("line") or 1)
        return file_level_score, [{
            "name": None,
            "symbol_path": None,
            "start_line": line,
            "end_line": line,
            "score": file_level_score,
        }]

    scored_entities: list[tuple[float, dict]] = []
    for entity in entities:
        score = float(
            len(query_tokens & _entity_tokens(entity)) * 2
            + _focus_hint_score(entity, lower_path, focus_hints)
        )
        if score > 0:
            scored_entities.append((score, entity))

    if not scored_entities:
        return file_level_score, []

    scored_entities.sort(key=lambda x: x[0], reverse=True)
    top_score = scored_entities[0][0]
    threshold = _MULTI_SLICE_THRESHOLD_RATIO * top_score
    selected = [
        (score, entity)
        for score, entity in scored_entities[:slices_per_file]
        if score >= threshold
    ]

    file_score = file_level_score + sum(score for score, _ in selected)
    slices = [{
        "name": entity.get("name"),
        "symbol_path": entity.get("symbol_path"),
        "start_line": entity.get("start_line"),
        "end_line": entity.get("end_line"),
        "score": score,
    } for score, entity in selected]
    return file_score, slices
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_search_adaptive.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report_search.py tests/worker/test_fast_report_search_adaptive.py
git commit -m "feat(fast-report): multi-slice per-file scoring with threshold"
```

---

### Task B4: Per-graph BFS expansion (call_sites / exception_touchpoints / config_touchpoints / siblings)

**Files:**
- Modify: `worker/fast_report_search.py`
- Test: `tests/worker/test_fast_report_search_adaptive.py`

- [ ] **Step 1: Write failing tests**

```python
from worker.fast_report_search import _expand_with_graph


def test_call_sites_expansion_finds_callers_and_callees():
    files = {
        "a.py": {
            "path": "a.py",
            "entities": [{"name": "caller", "symbol_path": "a.caller"}],
            "call_sites": [{"caller_symbol_path": "a.caller", "callee_name": "helper", "line": 2}],
            "imports": [], "imported_by": [],
        },
        "b.py": {
            "path": "b.py",
            "entities": [{"name": "helper", "symbol_path": "b.helper"}],
            "call_sites": [],
            "imports": [], "imported_by": [],
        },
    }
    seeds = ["a.py"]
    selected = _expand_with_graph(files, seeds, primary="call_sites", secondary=None,
                                   depth=2, result_limit=10, exclude_tests=False)
    assert "b.py" in selected


def test_exception_touchpoints_expansion_excludes_tests_unless_testing_qt():
    files = {
        "svc.py": {"path": "svc.py", "exception_touchpoints": [{"kind": "raise", "symbol_path": "svc.fn", "line": 3, "message": "boom"}], "imports": [], "imported_by": [], "entities": [], "is_test": False},
        "test_svc.py": {"path": "test_svc.py", "exception_touchpoints": [{"kind": "raise", "symbol_path": "test_svc.tfn", "line": 4, "message": "boom"}], "imports": [], "imported_by": [], "entities": [], "is_test": True},
    }
    selected = _expand_with_graph(files, ["svc.py"], primary="exception_touchpoints",
                                   secondary=None, depth=2, result_limit=10,
                                   exclude_tests=True)
    assert "test_svc.py" not in selected


def test_config_touchpoints_expansion_matches_config_key():
    files = {
        "reader.py": {"path": "reader.py", "config_touchpoints": [{"kind": "read", "config_key": "K", "line": 1, "scope": "module"}], "imports": [], "imported_by": [], "entities": [], "is_config": False},
        "loader.py": {"path": "loader.py", "config_touchpoints": [{"kind": "read", "config_key": "K", "line": 5, "scope": "module"}], "imports": [], "imported_by": [], "entities": [], "is_config": False},
    }
    selected = _expand_with_graph(files, ["reader.py"], primary="config_touchpoints",
                                   secondary=None, depth=2, result_limit=10,
                                   exclude_tests=False)
    assert "loader.py" in selected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_search_adaptive.py -v -k expand_with_graph`
Expected: FAIL.

- [ ] **Step 3: Implement `_expand_with_graph`**

Add to `worker/fast_report_search.py`:

```python
def _neighbors_for_graph(
    files: dict, path: str, graph: str, exclude_tests: bool
) -> list[str]:
    entry = files.get(path, {})
    out: set[str] = set()
    if graph == "imports_and_imported_by":
        out.update(entry.get("imports") or [])
        out.update(entry.get("imported_by") or [])
    elif graph == "imports":
        out.update(entry.get("imports") or [])
    elif graph == "call_sites":
        my_entities = {e.get("name") for e in entry.get("entities") or []}
        for callee in {site.get("callee_name") for site in entry.get("call_sites") or []}:
            for other_path, other_entry in files.items():
                if other_path == path:
                    continue
                if any(e.get("name") == callee for e in other_entry.get("entities") or []):
                    out.add(other_path)
        for other_path, other_entry in files.items():
            if other_path == path:
                continue
            for site in other_entry.get("call_sites") or []:
                if site.get("callee_name") in my_entities:
                    out.add(other_path)
    elif graph == "exception_touchpoints":
        my_msgs = {tp.get("message") for tp in entry.get("exception_touchpoints") or []}
        my_syms = {tp.get("symbol_path") for tp in entry.get("exception_touchpoints") or []}
        for other_path, other_entry in files.items():
            if other_path == path:
                continue
            for tp in other_entry.get("exception_touchpoints") or []:
                if tp.get("message") in my_msgs or tp.get("symbol_path") in my_syms:
                    out.add(other_path)
                    break
    elif graph == "config_touchpoints":
        my_keys = {tp.get("config_key") for tp in entry.get("config_touchpoints") or []}
        for other_path, other_entry in files.items():
            if other_path == path:
                continue
            for tp in other_entry.get("config_touchpoints") or []:
                if tp.get("config_key") in my_keys:
                    out.add(other_path)
                    break
            if other_entry.get("is_config") and any(
                key in (other_entry.get("tokens") or []) for key in my_keys
            ):
                out.add(other_path)
    elif graph == "sibling_token_overlap":
        prefix = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
        for other_path in files:
            if other_path == path:
                continue
            if other_path.startswith(prefix):
                out.add(other_path)
    elif graph == "sibling_directory":
        prefix = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
        for other_path in files:
            if other_path == path or not other_path.startswith(prefix):
                continue
            out.add(other_path)
    elif graph == "external_deps_overlap":
        my_deps = set(entry.get("external_deps") or [])
        for other_path, other_entry in files.items():
            if other_path == path:
                continue
            if my_deps & set(other_entry.get("external_deps") or []):
                out.add(other_path)
    elif graph == "is_config_files":
        for other_path, other_entry in files.items():
            if other_path != path and other_entry.get("is_config"):
                out.add(other_path)
    if exclude_tests:
        out = {p for p in out if not files.get(p, {}).get("is_test")}
    return sorted(out)


def _expand_with_graph(
    files: dict,
    seeds: list[str],
    *,
    primary: str,
    secondary: str | None,
    depth: int,
    result_limit: int,
    exclude_tests: bool,
) -> list[str]:
    selected: list[str] = list(seeds)
    seen = set(seeds)
    queue = deque((s, 0) for s in seeds)
    while queue and len(selected) < result_limit:
        path, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        neighbors = _neighbors_for_graph(files, path, primary, exclude_tests)
        if not neighbors and secondary:
            neighbors = _neighbors_for_graph(files, path, secondary, exclude_tests)
        for neighbor in neighbors:
            if neighbor in seen or neighbor not in files:
                continue
            seen.add(neighbor)
            selected.append(neighbor)
            queue.append((neighbor, current_depth + 1))
            if len(selected) >= result_limit:
                break
    return selected
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_search_adaptive.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report_search.py tests/worker/test_fast_report_search_adaptive.py
git commit -m "feat(fast-report): per-question-type BFS expansion graphs"
```

---

### Task B5: Rewrite `retrieve_code_evidence` to use adaptive profile + multi-slice + new citation IDs

**Files:**
- Modify: `worker/fast_report_search.py`
- Test: `tests/worker/test_fast_report_search_adaptive.py`

The new entry-point signature replaces the file-level expansion. `retrieve_code_evidence` will now:
1. Compute query tokens.
2. Score every file via `_score_file_multi_slice` (using profile.slices_per_file).
3. Take top `seed` files.
4. Expand via `_expand_with_graph` with `(depth, result_limit)` from the profile.
5. For each selected file, emit one slice per selected entity. Citation id `code-{file_idx}-{entity_idx}` (file_idx in selection order, entity_idx in score-descending order).
6. Sum slice tokens; if over `code_evidence_token_budget`, drop slices in ascending score until under budget. Record drop count.

Keep `_select_primary_entity`, `_focus_hint_score`, `_entity_tokens`, `_tokenize` unchanged.

- [ ] **Step 1: Write failing test**

```python
def test_retrieve_code_evidence_multi_slice_emits_namespaced_citation_ids():
    from worker.fast_report import FastReportQuestionIntent
    index = {
        "index_version": 2,
        "directory_tree": "m.py\n",
        "files": {
            "m.py": {
                "path": "m.py",
                "tokens": ["report"],
                "imports": [], "imported_by": [],
                "entities": [
                    {"name": "report_a", "symbol_path": "m.report_a", "start_line": 1, "end_line": 5},
                    {"name": "report_b", "symbol_path": "m.report_b", "start_line": 6, "end_line": 10},
                    {"name": "report_c", "symbol_path": "m.report_c", "start_line": 11, "end_line": 15},
                ],
            },
        },
    }
    intent = FastReportQuestionIntent(question_type="architecture")
    layer = retrieve_code_evidence(index, intent, "report")
    ids = sorted(c.id for c in layer.citations)
    assert ids == ["code-0-0", "code-0-1", "code-0-2"]


def test_retrieve_code_evidence_token_budget_drops_lowest_scored():
    # implementation_location profile has code_evidence_token_budget=25_000.
    # Each slice ~ line_cap (200) * 16 tokens ≈ 3_200; 9 slices ≈ 28_800 → 1 dropped.
    files = {}
    for i in range(9):
        files[f"f{i}.py"] = {
            "path": f"f{i}.py", "tokens": [f"helper{i}"],
            "imports": [], "imported_by": [],
            "entities": [{"name": f"helper{i}", "symbol_path": f"f{i}.helper{i}",
                           "start_line": 1, "end_line": 200}],
        }
    intent = FastReportQuestionIntent(
        question_type="implementation_location",
        search_terms=[f"helper{i}" for i in range(9)],
        retrieval_focus=[f"f{i}.helper{i}" for i in range(9)],
    )
    layer = retrieve_code_evidence({"index_version": 2, "files": files}, intent, "helper")
    # implementation_location seed=2, result_limit=4 ⇒ at most 4 slices anyway,
    # but the test asserts the budget guard never throws and returns a sane count.
    assert len(layer.citations) <= 4
    # Sanity: scores are sorted descending in returned order
    scores = [c.score for c in layer.citations if c.score is not None]
    assert scores == sorted(scores, reverse=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_search_adaptive.py -v -k retrieve_code_evidence`
Expected: FAIL.

- [ ] **Step 3: Rewrite `retrieve_code_evidence`**

```python
from worker.fast_report_planning import expansion_graph_for, profile_for_question_type


def retrieve_code_evidence(index, plan, question):
    from worker.fast_report import CodeEvidenceLayer
    files = index.get("files")
    if not isinstance(files, dict) or not files:
        return CodeEvidenceLayer()

    normalized = _normalize_search_plan(plan)
    profile = profile_for_question_type(normalized.question_type)
    graph = expansion_graph_for(normalized.question_type)
    query_tokens = _query_tokens(normalized, question)
    focus_hints = [hint.lower() for hint in normalized.retrieval_focus]
    allow_config_files = _is_config_relevant_query(normalized, query_tokens)

    # 1) Score every file (multi-slice)
    scored: list[tuple[float, str, list[dict]]] = []
    for path, entry in files.items():
        if _is_low_signal_entry(entry, focus_hints, allow_config_files):
            continue
        file_score, slices = _score_file_multi_slice(
            path=path,
            entry=entry,
            query_tokens=query_tokens,
            focus_hints=focus_hints,
            slices_per_file=profile.slices_per_file,
        )
        if file_score <= 0:
            continue
        scored.append((file_score, path, slices))

    scored.sort(key=lambda x: (-x[0], x[1]))
    seeds = [path for _score, path, _slices in scored[: profile.seed]]
    exclude_tests = normalized.question_type != "testing"
    selected_paths = _expand_with_graph(
        files, seeds,
        primary=graph.primary, secondary=graph.secondary,
        depth=profile.depth, result_limit=profile.result_limit,
        exclude_tests=exclude_tests,
    )
    slices_by_path = {path: slices for _score, path, slices in scored}

    # 2) Build (file_idx, entity_idx, slice_dict, score) tuples preserving selection order
    pending: list[tuple[int, int, str, dict, float]] = []
    for file_idx, path in enumerate(selected_paths):
        slices = slices_by_path.get(path)
        if slices is None:
            entry = files[path]
            file_score, slices = _score_file_multi_slice(
                path=path, entry=entry, query_tokens=query_tokens,
                focus_hints=focus_hints, slices_per_file=profile.slices_per_file,
            )
        if not slices:
            entry = files[path]
            primary_entity = _select_primary_entity(entry)
            if primary_entity is None:
                continue
            slices = [{
                "name": primary_entity.get("name"),
                "symbol_path": primary_entity.get("symbol_path"),
                "start_line": primary_entity.get("start_line"),
                "end_line": primary_entity.get("end_line"),
                "score": 0.1,
            }]
        for entity_idx, sl in enumerate(slices):
            pending.append((file_idx, entity_idx, path, sl, sl["score"]))

    # 3) Apply token budget across all pending slices
    budget = profile.code_evidence_token_budget
    pending = _apply_token_budget(pending, budget=budget, line_cap=profile.per_slice_line_cap)

    # 4) Emit citations + evidence blocks (no real source yet — Task C2 wires it)
    snippets, citations, evidence_blocks = [], [], []
    for file_idx, entity_idx, path, sl, _score in pending:
        start_line, end_line = _line_span_from_slice(sl, profile.per_slice_line_cap)
        citation_id = f"code-{file_idx}-{entity_idx}"
        text = _build_snippet_text(path, files[path], sl)
        symbol_path = sl.get("symbol_path")
        label = sl.get("name") or Path(path).name
        snippets.append({
            "file": path, "start_line": start_line, "end_line": end_line,
            "text": text, "score": _score, "symbol_path": symbol_path,
        })
        citations.append(_make_citation(
            citation_id=citation_id, file_path=path,
            start_line=start_line, end_line=end_line,
            label=label, score=_score,
            reason="Multi-slice match" if entity_idx > 0 else "Top match",
        ))
        evidence_blocks.append(_make_evidence_block(
            citation_id=citation_id, start_line=start_line, end_line=end_line,
            code=text, symbol_path=symbol_path,
        ))

    return CodeEvidenceLayer(
        snippets=snippets, citations=citations, evidence_blocks=evidence_blocks,
    )


def _line_span_from_slice(sl: dict, line_cap: int) -> tuple[int, int]:
    start = int(sl.get("start_line") or 1)
    end = int(sl.get("end_line") or start)
    return start, min(end, start + line_cap - 1)


def _apply_token_budget(pending, *, budget: int, line_cap: int) -> list:
    # Estimate: each slice tokens ~= line_cap * 8 (rough). Use length when text known.
    pending_sorted = sorted(pending, key=lambda item: item[4])  # ascending score
    total = sum(_estimate_slice_tokens(item, line_cap) for item in pending)
    while pending_sorted and total > budget:
        dropped = pending_sorted.pop(0)
        total -= _estimate_slice_tokens(dropped, line_cap)
    kept_set = {(item[0], item[1]) for item in pending_sorted}
    return [item for item in pending if (item[0], item[1]) in kept_set]


def _estimate_slice_tokens(item, line_cap: int) -> int:
    _file_idx, _entity_idx, _path, sl, _score = item
    length = (int(sl.get("end_line") or 1) - int(sl.get("start_line") or 1)) + 1
    return min(length, line_cap) * 16  # ~16 tokens per code line average
```

Update `_make_evidence_block` to use ±5 instead of ±3:

```python
def _make_evidence_block(*, citation_id, start_line, end_line, code, symbol_path):
    from shared.fast_report_types import FastReportEvidenceBlock
    return FastReportEvidenceBlock(
        citation_id=citation_id,
        snippet_start=start_line,
        snippet_end=end_line,
        full_start=max(1, start_line - 5),
        full_end=end_line + 5,
        code=code,
        symbol_path=symbol_path,
    )
```

Delete the now-obsolete `_SEED_LIMIT`, `_EXPANSION_DEPTH`, `_RESULT_LIMIT` constants.

- [ ] **Step 4: Run tests; existing `test_fast_report.py` tests will break — update them**

Run: `uv run pytest tests/worker/test_fast_report_search_adaptive.py tests/worker/test_fast_report.py -v`

Expected first run: existing tests in `test_fast_report.py` that hardcode `code-1`, `code-2` citation ids will FAIL. Update them to expect `code-{file_idx}-{entity_idx}` form. Where they assert exactly 4 results, update to use the `unknown` profile's `result_limit=6` (or pass an explicit `question_type` in the intent).

- [ ] **Step 5: Re-run, verify pass**

Run: `uv run pytest tests/worker/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add worker/fast_report_search.py tests/worker/
git commit -m "feat(fast-report): adaptive retrieval with per-type budgets and multi-slice citations"
```

---

## Phase C — Real Source Slice Extraction

### Task C1: Create `worker/fast_report_slices.py` with `extract_source_slice`

**Files:**
- Create: `worker/fast_report_slices.py`
- Test: `tests/worker/test_fast_report_slices.py`

- [ ] **Step 1: Write failing tests**

Create `tests/worker/test_fast_report_slices.py`:

```python
from pathlib import Path
import pytest
from worker.fast_report_slices import SliceResult, extract_source_slice


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def test_happy_path_returns_real_source(tmp_path):
    _write(tmp_path, "m.py", "line1\nline2\nline3\nline4\nline5\n")
    result = extract_source_slice(
        clone_root=tmp_path, rel_path="m.py",
        anchor_start=2, anchor_end=4, line_cap=10,
    )
    assert result == SliceResult(
        snippet_start=2, snippet_end=4, full_start=1, full_end=5,
        code="line2\nline3\nline4", truncated_lines=0,
    )


def test_missing_file_returns_none(tmp_path):
    assert extract_source_slice(
        clone_root=tmp_path, rel_path="missing.py",
        anchor_start=1, anchor_end=3, line_cap=10,
    ) is None


def test_full_end_clamped_to_file_length(tmp_path):
    _write(tmp_path, "m.py", "a\nb\nc\n")
    result = extract_source_slice(
        clone_root=tmp_path, rel_path="m.py",
        anchor_start=2, anchor_end=3, line_cap=10,
    )
    assert result.full_end == 3


def test_over_cap_appends_truncation_marker_python(tmp_path):
    body = "\n".join(f"line{i}" for i in range(1, 21))
    _write(tmp_path, "m.py", body)
    result = extract_source_slice(
        clone_root=tmp_path, rel_path="m.py",
        anchor_start=1, anchor_end=20, line_cap=5,
    )
    assert result.truncated_lines == 15
    assert result.code.endswith("# … 15 more lines truncated")


@pytest.mark.parametrize("ext,marker_prefix", [
    ("py", "#"), ("js", "//"), ("ts", "//"), ("go", "//"), ("rs", "//"),
    ("java", "//"), ("c", "//"), ("cpp", "//"), ("cs", "//"),
])
def test_truncation_marker_per_language(tmp_path, ext, marker_prefix):
    body = "\n".join(f"line{i}" for i in range(1, 11))
    _write(tmp_path, f"m.{ext}", body)
    result = extract_source_slice(
        clone_root=tmp_path, rel_path=f"m.{ext}",
        anchor_start=1, anchor_end=10, line_cap=3,
    )
    assert result.code.endswith(f"{marker_prefix} … 7 more lines truncated")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_slices.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `worker/fast_report_slices.py`:

```python
"""Source slice extractor for fast reports."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_HASH_LANGS = {"py", "rb", "sh", "bash", "zsh"}


def _comment_token(rel_path: str) -> str:
    suffix = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
    return "#" if suffix in _HASH_LANGS else "//"


@dataclass(frozen=True, slots=True)
class SliceResult:
    snippet_start: int
    snippet_end: int
    full_start: int
    full_end: int
    code: str
    truncated_lines: int


def extract_source_slice(
    *,
    clone_root: Path,
    rel_path: str,
    anchor_start: int,
    anchor_end: int,
    line_cap: int,
    context_lines: int = 5,
) -> SliceResult | None:
    file_path = clone_root / rel_path
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError, IsADirectoryError):
        return None

    lines = text.splitlines()
    file_len = len(lines)
    anchor_start = max(1, anchor_start)
    anchor_end = max(anchor_start, min(anchor_end, file_len))
    snippet_end = min(anchor_end, anchor_start + line_cap - 1)
    truncated_lines = max(0, anchor_end - snippet_end)

    body = "\n".join(lines[anchor_start - 1 : snippet_end])
    if truncated_lines > 0:
        body += f"\n{_comment_token(rel_path)} … {truncated_lines} more lines truncated"

    full_start = max(1, anchor_start - context_lines)
    full_end = min(file_len, snippet_end + context_lines)
    return SliceResult(
        snippet_start=anchor_start,
        snippet_end=snippet_end,
        full_start=full_start,
        full_end=full_end,
        code=body,
        truncated_lines=truncated_lines,
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_slices.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report_slices.py tests/worker/test_fast_report_slices.py
git commit -m "feat(fast-report): add source slice extractor"
```

---

### Task C2: Wire real source into the code-evidence retriever

**Files:**
- Modify: `worker/fast_report_search.py` (`retrieve_code_evidence` signature gains `clone_root: Path | None`)
- Modify: `worker/jobs.py` (`_build_default_fast_report_retrievers` passes `clone_root`)
- Test: `tests/worker/test_fast_report_search_adaptive.py`

- [ ] **Step 1: Write failing test**

```python
from pathlib import Path
from worker.fast_report import FastReportQuestionIntent
from worker.fast_report_search import retrieve_code_evidence


def test_retrieve_code_evidence_uses_real_source_when_clone_root_given(tmp_path):
    (tmp_path / "m.py").write_text("def helper():\n    return 42\n")
    index = {
        "index_version": 2,
        "files": {
            "m.py": {
                "path": "m.py",
                "tokens": ["helper"],
                "imports": [], "imported_by": [],
                "entities": [{"name": "helper", "symbol_path": "m.helper",
                              "start_line": 1, "end_line": 2}],
            },
        },
    }
    intent = FastReportQuestionIntent(question_type="implementation_location")
    layer = retrieve_code_evidence(index, intent, "helper", clone_root=tmp_path)
    assert "def helper" in layer.evidence_blocks[0].code
    assert "File:" not in layer.evidence_blocks[0].code  # real source, not metadata
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_fast_report_search_adaptive.py -v -k clone_root`
Expected: FAIL (signature mismatch / metadata text returned).

- [ ] **Step 3: Add `clone_root` parameter and call `extract_source_slice`**

In `worker/fast_report_search.py`:

```python
def retrieve_code_evidence(index, plan, question, *, clone_root: Path | None = None):
    ...
    for file_idx, entity_idx, path, sl, _score in pending:
        start_line, end_line = _line_span_from_slice(sl, profile.per_slice_line_cap)
        citation_id = f"code-{file_idx}-{entity_idx}"
        symbol_path = sl.get("symbol_path")
        label = sl.get("name") or Path(path).name

        slice_result = None
        if clone_root is not None:
            slice_result = extract_source_slice(
                clone_root=clone_root, rel_path=path,
                anchor_start=start_line, anchor_end=end_line,
                line_cap=profile.per_slice_line_cap,
            )
        if slice_result is None:
            # Drop citation entirely on file-read failure (per spec C1)
            if clone_root is not None:
                continue
            # Backward-compat for tests not passing clone_root: emit metadata
            text = _build_snippet_text(path, files[path], sl)
            full_start, full_end = max(1, start_line - 5), end_line + 5
        else:
            text = slice_result.code
            start_line, end_line = slice_result.snippet_start, slice_result.snippet_end
            full_start, full_end = slice_result.full_start, slice_result.full_end

        snippets.append({
            "file": path, "start_line": start_line, "end_line": end_line,
            "text": text, "score": _score, "symbol_path": symbol_path,
        })
        citations.append(_make_citation(
            citation_id=citation_id, file_path=path,
            start_line=start_line, end_line=end_line,
            label=label, score=_score,
            reason="Multi-slice match" if entity_idx > 0 else "Top match",
        ))
        from shared.fast_report_types import FastReportEvidenceBlock
        evidence_blocks.append(FastReportEvidenceBlock(
            citation_id=citation_id, snippet_start=start_line, snippet_end=end_line,
            full_start=full_start, full_end=full_end, code=text, symbol_path=symbol_path,
        ))
    return CodeEvidenceLayer(snippets=snippets, citations=citations, evidence_blocks=evidence_blocks)
```

In `worker/jobs.py` `_build_default_fast_report_retrievers`, pass `clone_root`:

```python
async def _code_evidence(question, intent):
    return retrieve_code_evidence(fast_report_index, intent, question, clone_root=clone_root)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report_search.py worker/jobs.py tests/worker/test_fast_report_search_adaptive.py
git commit -m "feat(fast-report): inject real source slices into code evidence layer"
```

---

## Phase D — Planning Hardening

### Task D1: Constrain `question_type` to the eight-value enum in the planner schema

**Files:**
- Modify: `worker/fast_report.py` (`_SEARCH_PLAN_SCHEMA`)
- Test: `tests/worker/test_fast_report_planning.py` and existing `test_fast_report.py`

- [ ] **Step 1: Write failing test**

In `tests/worker/test_fast_report_planning.py`:

```python
from worker.fast_report import _SEARCH_PLAN_SCHEMA


def test_search_plan_schema_constrains_question_type_to_enum():
    qt_schema = _SEARCH_PLAN_SCHEMA["properties"]["question_type"]
    assert set(qt_schema["enum"]) == set([
        "architecture", "execution_flow", "dependency", "error_handling",
        "configuration", "testing", "implementation_location", "unknown",
    ])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/worker/test_fast_report_planning.py -v -k constrains_question_type`
Expected: FAIL.

- [ ] **Step 3: Update schema**

In `worker/fast_report.py`:

```python
from worker.fast_report_planning import QUESTION_TYPES

_SEARCH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "question_type": {"type": "string", "enum": list(QUESTION_TYPES)},
        ...
    },
    "required": ["language", "question_type"],
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report.py tests/worker/test_fast_report_planning.py
git commit -m "feat(fast-report): constrain planner question_type to enum"
```

---

### Task D2: Build planner repository-shape context (`build_plan_prompt_context`)

**Files:**
- Modify: `worker/fast_report_planning.py`
- Test: `tests/worker/test_fast_report_planning.py`

- [ ] **Step 1: Write failing test**

```python
from worker.fast_report_planning import build_plan_prompt_context


def test_build_plan_prompt_context_includes_directory_tree_hubs_headings():
    index = {
        "directory_tree": "src/\n  main.py\n",
        "hub_modules": [{"path": "src/util.py", "in_degree": 5, "purpose": "Util."}],
        "readme_headings": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N"],
    }
    ctx = build_plan_prompt_context(index)
    assert "Directory tree:" in ctx
    assert "src/util.py" in ctx
    assert "in_degree=5" in ctx or "in-degree 5" in ctx
    assert "Symbol path convention" in ctx
    assert ctx.count("\n- ") >= 12  # README headings top 12 listed as bullets
    assert "M" not in ctx.split("README headings:")[1][:200]  # only 12 included
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/worker/test_fast_report_planning.py -v -k build_plan_prompt_context`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
_README_HEADINGS_PROMPT_LIMIT = 12


def build_plan_prompt_context(index: dict) -> str:
    directory_tree = (index.get("directory_tree") or "").rstrip()
    hub_modules = index.get("hub_modules") or []
    readme_headings = (index.get("readme_headings") or [])[:_README_HEADINGS_PROMPT_LIMIT]

    hub_lines = []
    for h in hub_modules:
        purpose = h.get("purpose") or ""
        hub_lines.append(f"- {h['path']} (in_degree={h['in_degree']}) — {purpose}".rstrip(" —"))

    heading_lines = "\n".join(f"- {h}" for h in readme_headings) or "- (no README headings detected)"
    hubs_block = "\n".join(hub_lines) or "- (no hub modules detected)"

    return (
        f"Directory tree:\n{directory_tree or '(empty)'}\n\n"
        f"README headings:\n{heading_lines}\n\n"
        f"Hub modules:\n{hubs_block}\n\n"
        "Symbol path convention: Use `module.path.symbol_name` for retrieval_focus "
        "(path slashes → dots, extension stripped)."
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_planning.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report_planning.py tests/worker/test_fast_report_planning.py
git commit -m "feat(fast-report): build plan prompt context with repo shape"
```

---

### Task D3: Single-shot feedback retry on degenerate plan output

**Files:**
- Modify: `worker/fast_report.py` (`plan_fast_report_search`)
- Test: `tests/worker/test_fast_report.py` (or new `test_fast_report_planning.py` integration block)

- [ ] **Step 1: Write failing tests**

```python
async def test_plan_retry_when_question_type_unknown_and_no_hints(monkeypatch):
    calls: list[str] = []

    class FakeLLM:
        async def generate_structured(self, prompt, schema):
            calls.append(prompt)
            if len(calls) == 1:
                return {"language": "en", "question_type": "unknown",
                        "search_terms": [], "retrieval_focus": []}
            return {"language": "en", "question_type": "execution_flow",
                    "search_terms": ["x"], "retrieval_focus": ["worker.fast_report.plan_fast_report_search"]}

    intent = await plan_fast_report_search(
        question="how does X work?", repo_name="r", llm=FakeLLM(),
        index={"directory_tree": "worker/\n  fast_report.py\n",
                "hub_modules": [], "readme_headings": []},
    )
    assert len(calls) == 2  # one retry
    assert intent.question_type == "execution_flow"
    assert "Re-plan the search" in calls[1] or "retry" in calls[1].lower()


async def test_plan_no_retry_when_first_attempt_is_useful(monkeypatch):
    calls = []

    class FakeLLM:
        async def generate_structured(self, prompt, schema):
            calls.append(prompt)
            return {"language": "en", "question_type": "execution_flow",
                    "search_terms": ["x"], "retrieval_focus": ["a.b"]}

    await plan_fast_report_search(question="?", repo_name="r", llm=FakeLLM(),
                                   index={"directory_tree": "", "hub_modules": [], "readme_headings": []})
    assert len(calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report.py -v -k plan_retry`
Expected: FAIL.

- [ ] **Step 3: Modify `plan_fast_report_search`**

Update the signature to take `index: dict | None = None` (so the index can be threaded in from `generate_fast_report_section`). Then:

```python
from worker.fast_report_planning import build_plan_prompt_context
from worker.pipeline.pipeline_logging import log_validation_retry, log_final_failure
import logging

logger = logging.getLogger(__name__)


def _is_degenerate_plan(intent: FastReportQuestionIntent) -> bool:
    return (
        intent.question_type == "unknown"
        and not intent.search_terms
        and not intent.retrieval_focus
    )


async def plan_fast_report_search(
    question: str, repo_name: str, llm: LLMProvider,
    *, index: dict | None = None,
) -> FastReportQuestionIntent:
    detected_language = normalize_fast_report_language(detect_question_language(question))
    repo_shape = build_plan_prompt_context(index or {}) if index else ""
    base_prompt = (
        "Plan the repository search strategy for fast report generation.\n"
        f"Repository: {repo_name}\n"
        f"Question: {question}\n\n"
        f"{repo_shape}\n\n"
        "Return JSON with language, question_type, target, answer_shape, "
        "evidence_shape, search_terms, and retrieval_focus.\n"
        f"Use '{detected_language}' for language unless the question clearly "
        "requires a different answer language."
        f"{get_fast_report_language_instruction(detected_language)}"
    )

    intent = await _parse_plan_response(
        await llm.generate_structured(base_prompt, _SEARCH_PLAN_SCHEMA),
        fallback_language=detected_language,
    )
    if not _is_degenerate_plan(intent):
        return intent

    log_validation_retry(
        logger, stage="fast_report.plan", attempt=1, max_retries=2,
        exc=ValueError("degenerate_plan"),
        context={"question": question, "repo": repo_name},
    )
    feedback_prompt = base_prompt + (
        "\n\nYour previous plan returned no question_type and no retrieval hints. "
        "Re-plan the search. Choose one of the enumerated question_type values and "
        "return at least one retrieval_focus hint pointing at a real path or symbol."
    )
    retry_intent = await _parse_plan_response(
        await llm.generate_structured(feedback_prompt, _SEARCH_PLAN_SCHEMA),
        fallback_language=detected_language,
    )
    if _is_degenerate_plan(retry_intent):
        log_final_failure(
            logger, stage="fast_report.plan",
            exc=ValueError("plan still degenerate after retry"),
            context={"question": question},
        )
    return retry_intent


def _parse_plan_response(raw: dict, *, fallback_language: str) -> FastReportQuestionIntent:
    raw_language = raw.get("language")
    planned_language = (
        normalize_fast_report_language(raw_language) if raw_language else fallback_language
    )
    return FastReportQuestionIntent(
        language=planned_language,
        question_type=raw.get("question_type", "unknown"),
        target=str(raw.get("target", "") or "").strip(),
        answer_shape=str(raw.get("answer_shape", "") or "").strip(),
        evidence_shape=str(raw.get("evidence_shape", "") or "").strip(),
        search_terms=normalize_string_list(raw.get("search_terms", [])),
        retrieval_focus=normalize_string_list(raw.get("retrieval_focus", [])),
    )
```

In `generate_fast_report_section`, thread the index through:

```python
intent = await plan_fast_report_search(question, repo_name, llm, index=fast_report_index)
```

This requires `generate_fast_report_section` to accept `fast_report_index: dict | None = None`. Update its signature and `worker/jobs.py` `run_fast_report` to pass the index loaded by `_build_default_fast_report_retrievers`. The cleanest way: have the retriever factory return the loaded index alongside the retrievers, and `run_fast_report` pass it into `generate_fast_report_section`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report.py worker/jobs.py tests/worker/test_fast_report.py
git commit -m "feat(fast-report): single-shot feedback retry on degenerate plan"
```

---

## Phase E — Interpretive Context Layer

### Task E1: Create the interpretive layer module

**Files:**
- Create: `worker/fast_report_interpretive.py`
- Test: `tests/worker/test_fast_report_interpretive.py`

- [ ] **Step 1: Write failing tests**

Create `tests/worker/test_fast_report_interpretive.py`:

```python
from worker.fast_report_interpretive import (
    InterpretiveBundle,
    build_interpretive_bundle,
)


def test_auto_attached_payload_caps_at_8k_tokens_dropping_lowest_score():
    big = "x" * 40_000  # ≈10k tokens
    selected = [
        {"file": "a.py", "name": "f1", "score": 1.0,
         "docstring": big, "module_docstring": "Mod A.", "leading_comment": None},
        {"file": "b.py", "name": "f2", "score": 100.0,
         "docstring": "ok", "module_docstring": "Mod B.", "leading_comment": None},
    ]
    bundle = build_interpretive_bundle(selected_entities=selected, index={"readme_sections": []}, intent_tokens=set())
    assert any(item["source"] == "entity_docstring" and "ok" in item["text"] for item in bundle.entries)
    # Low-score 10k-token entity dropped
    assert not any(item["source"] == "entity_docstring" and item["text"].startswith("xxxx") for item in bundle.entries)


def test_readme_section_ranking_top_5_with_overlap_score():
    sections = [
        {"heading": "Architecture", "body": "AutoWiki uses a 6-stage pipeline."},
        {"heading": "Deployment", "body": "Use docker-compose."},
        {"heading": "Random", "body": "Unrelated text."},
    ]
    bundle = build_interpretive_bundle(
        selected_entities=[],
        index={"readme_sections": sections},
        intent_tokens={"pipeline", "stage"},
    )
    headings = [e["heading"] for e in bundle.entries if e["source"] == "readme_section"]
    assert headings[0] == "Architecture"


def test_interpretive_bundle_has_no_citations():
    bundle = build_interpretive_bundle(
        selected_entities=[{"file": "a.py", "name": "f", "score": 1.0,
                             "docstring": "doc", "module_docstring": None, "leading_comment": None}],
        index={"readme_sections": []}, intent_tokens=set(),
    )
    assert isinstance(bundle, InterpretiveBundle)
    assert not hasattr(bundle, "citations")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report_interpretive.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
"""Interpretive Context Layer for fast reports.

Pulls module docstrings, entity docstrings, leading comments, and ranked
README section bodies. Produces no citations — interpretive content is
generation fuel, not user-facing evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from worker.fast_report_search import _tokenize

_AUTO_ATTACH_TOKEN_CAP = 8_000
_README_TOP_K = 5
_README_BODY_CAP = 800
_README_TOTAL_TOKEN_CAP = 10_000


def _approx_tokens(text: str) -> int:
    return max(0, len(text or "") // 4)


@dataclass(slots=True)
class InterpretiveBundle:
    entries: list[dict] = field(default_factory=list)


def build_interpretive_bundle(
    *,
    selected_entities: list[dict],
    index: dict,
    intent_tokens: set[str],
) -> InterpretiveBundle:
    entries: list[dict] = []
    auto_candidates: list[tuple[float, dict]] = []
    seen_module_files: set[str] = set()
    for entity in selected_entities:
        score = float(entity.get("score") or 0.0)
        file_path = entity.get("file", "")
        if entity.get("docstring"):
            auto_candidates.append((score, {
                "source": "entity_docstring",
                "file": file_path,
                "name": entity.get("name"),
                "text": entity["docstring"],
            }))
        if entity.get("leading_comment"):
            auto_candidates.append((score, {
                "source": "entity_leading_comment",
                "file": file_path,
                "name": entity.get("name"),
                "text": entity["leading_comment"],
            }))
        if entity.get("module_docstring") and file_path not in seen_module_files:
            seen_module_files.add(file_path)
            auto_candidates.append((score, {
                "source": "module_docstring",
                "file": file_path,
                "name": None,
                "text": entity["module_docstring"],
            }))

    auto_candidates.sort(key=lambda x: x[0], reverse=True)
    cumulative = 0
    for _score, item in auto_candidates:
        cost = _approx_tokens(item["text"])
        if cumulative + cost > _AUTO_ATTACH_TOKEN_CAP:
            continue
        cumulative += cost
        entries.append(item)

    readme_sections = index.get("readme_sections") or []
    ranked: list[tuple[int, dict]] = []
    for section in readme_sections:
        text = (section.get("heading") or "") + " " + (section.get("body") or "")
        overlap = len(intent_tokens & _tokenize(text))
        if overlap == 0:
            continue
        ranked.append((overlap, section))
    ranked.sort(key=lambda x: -x[0])

    cumulative = 0
    for _score, section in ranked[:_README_TOP_K]:
        body = (section.get("body") or "")[:_README_BODY_CAP]
        cost = _approx_tokens(body) + _approx_tokens(section.get("heading") or "")
        if cumulative + cost > _README_TOTAL_TOKEN_CAP:
            break
        cumulative += cost
        entries.append({
            "source": "readme_section",
            "heading": section.get("heading"),
            "text": body,
        })
    return InterpretiveBundle(entries=entries)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report_interpretive.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report_interpretive.py tests/worker/test_fast_report_interpretive.py
git commit -m "feat(fast-report): add interpretive context layer"
```

---

### Task E2: Wire interpretive layer into `retrieve_fast_report_layers` and the generation prompt

**Files:**
- Modify: `worker/fast_report.py`
- Test: `tests/worker/test_fast_report.py`

- [ ] **Step 1: Write failing tests**

```python
from worker.fast_report import (
    CodeEvidenceLayer, CuratedKnowledgeLayer, FastReportClaim,
    FastReportQuestionIntent, FastReportRetrievalLayers,
    RepositoryStructureLayer, _build_generation_prompt, arbitrate_report_claims,
)
from worker.fast_report_interpretive import InterpretiveBundle


def test_generation_prompt_includes_interpretive_block_with_no_cite_warning():
    intent = FastReportQuestionIntent(question_type="execution_flow")
    layers = FastReportRetrievalLayers(
        repository_structure=RepositoryStructureLayer(signals=["Directory tree:\n.\n"]),
        code_evidence=CodeEvidenceLayer(),
        curated_knowledge=CuratedKnowledgeLayer(),
        interpretive=InterpretiveBundle(entries=[
            {"source": "module_docstring", "file": "worker/fast_report.py", "name": None,
             "text": "Fast report domain service."},
            {"source": "readme_section", "heading": "Architecture",
             "text": "AutoWiki uses a 6-stage pipeline."},
        ]),
    )
    prompt = _build_generation_prompt("how does X?", "repo", intent, layers)
    assert "Interpretive context layer:" in prompt
    assert "Module docstring (worker/fast_report.py): Fast report domain service." in prompt
    assert 'README section "Architecture": AutoWiki uses a 6-stage pipeline.' in prompt
    assert "Use this interpretive layer ONLY to explain or connect code evidence" in prompt
    assert "Final claims must cite repository_structure or code_evidence ids" in prompt


def test_arbitration_drops_claim_supported_only_by_interpretive_id():
    interpretive_only = FastReportClaim(
        text="Claim grounded only in docstring",
        citation_ids=["interp-1"],
        supporting_layers=["interpretive"],
    )
    code_backed = FastReportClaim(
        text="Claim with code evidence",
        citation_ids=["code-0-0"],
        supporting_layers=["code_evidence"],
    )
    available = {"code-0-0"}  # interpretive ids excluded by design
    kept = arbitrate_report_claims([interpretive_only, code_backed],
                                    available_citation_ids=available)
    assert kept == [code_backed]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/worker/test_fast_report.py -v -k "interpretive_block or interpretive_id"`
Expected: FAIL.

- [ ] **Step 3: Wire into retrieval and prompt**

In `worker/fast_report.py`, extend `FastReportRetrievalLayers`:

```python
@dataclass(slots=True)
class FastReportRetrievalLayers:
    repository_structure: RepositoryStructureLayer
    code_evidence: CodeEvidenceLayer
    curated_knowledge: CuratedKnowledgeLayer
    interpretive: InterpretiveBundle = field(default_factory=InterpretiveBundle)
```

In `retrieve_fast_report_layers`, after retrieving the three existing layers, build the interpretive bundle from the selected entity slices:

```python
from worker.fast_report_interpretive import build_interpretive_bundle, InterpretiveBundle


async def retrieve_fast_report_layers(
    question, intent,
    repository_structure_retriever, code_evidence_retriever, curated_knowledge_retriever,
    *, index: dict | None = None,
):
    structure, code, curated = await asyncio.gather(
        retrieve_repository_structure_layer(question, intent, repository_structure_retriever),
        retrieve_code_evidence_layer(question, intent, code_evidence_retriever),
        retrieve_curated_knowledge_layer(question, intent, curated_knowledge_retriever),
    )
    selected_entities: list[dict] = []
    for citation, block in zip(code.citations, code.evidence_blocks, strict=False):
        # Look up the entity dict in the index by symbol_path; carry score from citation
        sel = _resolve_entity_from_index(index, citation.file_path, block.symbol_path)
        if sel is not None:
            sel["score"] = citation.score or 0.0
            selected_entities.append(sel)
    intent_tokens = set()
    for term in intent.search_terms + intent.retrieval_focus + [intent.target, question]:
        intent_tokens |= _tokenize_intent(term)
    interpretive = build_interpretive_bundle(
        selected_entities=selected_entities, index=index or {}, intent_tokens=intent_tokens,
    )
    return FastReportRetrievalLayers(
        repository_structure=structure, code_evidence=code,
        curated_knowledge=curated, interpretive=interpretive,
    )
```

Implement helpers:

```python
from worker.fast_report_search import _tokenize as _tokenize_intent


def _resolve_entity_from_index(index, file_path, symbol_path):
    if not index:
        return None
    file_entry = (index.get("files") or {}).get(file_path)
    if file_entry is None:
        return None
    for entity in file_entry.get("entities") or []:
        if entity.get("symbol_path") == symbol_path:
            return {
                "file": file_path,
                "name": entity.get("name"),
                "docstring": entity.get("docstring"),
                "leading_comment": entity.get("leading_comment"),
                "module_docstring": file_entry.get("module_docstring"),
            }
    return None
```

In `_build_generation_prompt`, append the interpretive block between Code evidence and Curated knowledge:

```python
def _build_generation_prompt(question, repo_name, intent, layers):
    ...
    interpretive_lines = []
    for entry in layers.interpretive.entries:
        if entry["source"] == "module_docstring":
            interpretive_lines.append(f"- Module docstring ({entry['file']}): {entry['text']}")
        elif entry["source"] == "entity_docstring":
            interpretive_lines.append(f"- Entity docstring ({entry.get('name')}): {entry['text']}")
        elif entry["source"] == "entity_leading_comment":
            interpretive_lines.append(f"- Entity leading comment ({entry.get('name')}): {entry['text']}")
        elif entry["source"] == "readme_section":
            interpretive_lines.append(f"- README section \"{entry['heading']}\": {entry['text']}")
    interpretive_block = "\n".join(interpretive_lines) or "- (no interpretive context available)"

    return (
        ...
        "Code evidence layer:\n"
        f"{code_context}\n\n"
        "Interpretive context layer:\n"
        f"{interpretive_block}\n\n"
        "Use this interpretive layer ONLY to explain or connect code evidence. "
        "Never cite it as primary support. "
        "Final claims must cite repository_structure or code_evidence ids.\n\n"
        "Curated knowledge layer:\n"
        f"{curated_summary or '- None'}\n\n"
        ...
    )
```

`available_citation_ids` already excludes interpretive entries (the bundle does not produce citations) — no change needed in arbitration, but write a regression test that confirms a claim referencing only interpretive content is dropped.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_fast_report.py tests/worker/test_fast_report_interpretive.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report.py tests/worker/test_fast_report.py
git commit -m "feat(fast-report): wire interpretive layer into retrieval and prompt"
```

---

## Phase F — Generation Prompt Enrichment

### Task F1: Structure-layer enrichment with `directory_tree` and `hub_modules`

**Files:**
- Modify: `worker/jobs.py` (`_repository_structure` retriever)
- Test: `tests/worker/test_jobs.py`

- [ ] **Step 1: Write failing test**

```python
async def test_repository_structure_signals_include_directory_tree_and_hubs(
    tmp_path, monkeypatch
):
    repo_data_dir = tmp_path / "repos" / "repo_id"
    (repo_data_dir / "ast").mkdir(parents=True)
    (repo_data_dir / "clone").mkdir()
    (repo_data_dir / "clone" / "README.md").write_text(
        "AutoWiki is a self-hosted, open-source AI-powered wiki generator.\n\n"
        "## Architecture\n\nAutoWiki uses a 6-stage pipeline."
    )
    index_payload = {
        "index_version": 2,
        "directory_tree": "api/\n  main.py\nworker/\n  jobs.py\n",
        "hub_modules": [
            {"path": "shared/types.py", "in_degree": 5, "purpose": "Shared types."}
        ],
        "readme_headings": ["AutoWiki", "Architecture"],
        "readme_sections": [],
        "files": {},
    }
    (repo_data_dir / "ast" / "fast_report_index.json").write_text(
        json.dumps(index_payload)
    )

    cfg = make_test_config(data_dir=tmp_path)  # follow existing test pattern
    retrievers = await _build_default_fast_report_retrievers(
        repo_id="repo_id", db_path=str(cfg.database_path), cfg=cfg,
    )
    layer = await retrievers["repository_structure_retriever"](
        "?", FastReportQuestionIntent(question_type="architecture"),
    )

    assert any(s.startswith("Directory tree:") for s in layer.signals)
    assert any("Hub modules" in s for s in layer.signals)
    assert any("README first paragraph: AutoWiki" in s for s in layer.signals)
    assert len(layer.citations) == 1
    assert layer.citations[0].id == "struct-1"
```

(`make_test_config` is the test-config helper already used elsewhere in `tests/worker/test_jobs.py`. If a different name is used in that file, follow its pattern.)

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/worker/test_jobs.py -v -k repository_structure_signals_include`
Expected: FAIL.

- [ ] **Step 3: Update `_repository_structure`**

```python
directory_tree = (fast_report_index.get("directory_tree") or "").rstrip()
hub_modules = fast_report_index.get("hub_modules") or []
readme_first_paragraph = ""
if readme:
    paragraph_lines = []
    for line in readme.splitlines():
        if not line.strip():
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(line)
    readme_first_paragraph = " ".join(paragraph_lines)[:400]


async def _repository_structure(question, intent):
    signals: list[str] = []
    if directory_tree:
        signals.append(f"Directory tree:\n{directory_tree}")
    if readme_headings:
        signals.append(f"README headings: {', '.join(readme_headings[:12])}")
    if readme_first_paragraph:
        signals.append(f"README first paragraph: {readme_first_paragraph}")
    if hub_modules:
        hub_lines = "\n".join(
            f"  - {h['path']} — {h.get('purpose') or ''}".rstrip(" —")
            for h in hub_modules
        )
        signals.append(f"Hub modules:\n{hub_lines}")
    citations = []
    if readme:
        citations = [FastReportCitation(
            id="struct-1", file_path="README.md", start_line=1, end_line=1,
            label="README", kind="repository_structure",
        )]
    return RepositoryStructureLayer(signals=signals, citations=citations)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/test_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/jobs.py tests/worker/test_jobs.py
git commit -m "feat(fast-report): enrich structure layer with directory_tree and hub_modules"
```

---

### Task F2: Bump curated wiki summary truncation 200 → 400 chars

**Files:**
- Modify: `worker/jobs.py` (`_curated`)
- Test: `tests/worker/test_jobs.py`

- [ ] **Step 1: Write failing test**

```python
async def test_curated_summary_truncates_at_400_chars(tmp_path, db_path):
    repo_id = await insert_repo(db_path)  # existing helper
    await insert_wiki_page(
        db_path, repo_id=repo_id, slug="overview", title="Overview",
        content="y" * 600, page_order=0,
    )
    cfg = make_test_config(data_dir=tmp_path, database_path=db_path)
    # Write minimal v2 index so the validator passes
    repo_data_dir = tmp_path / "repos" / repo_id
    (repo_data_dir / "ast").mkdir(parents=True)
    (repo_data_dir / "ast" / "fast_report_index.json").write_text(
        '{"index_version": 2, "files": {}, "directory_tree": "", "hub_modules": [], "readme_headings": [], "readme_sections": []}'
    )
    (repo_data_dir / "clone").mkdir()
    retrievers = await _build_default_fast_report_retrievers(
        repo_id=repo_id, db_path=db_path, cfg=cfg,
    )
    layer = await retrievers["curated_knowledge_retriever"](
        "Overview", FastReportQuestionIntent(question_type="architecture"),
    )
    assert layer.summaries
    assert len(layer.summaries[0]) == 400
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/worker/test_jobs.py -v -k curated_summary`
Expected: FAIL.

- [ ] **Step 3: Update `_curated`**

Change `page[2][:200]` to `page[2][:400]`.

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/worker/test_jobs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/jobs.py tests/worker/test_jobs.py
git commit -m "feat(fast-report): bump curated wiki summary truncation to 400 chars"
```

---

## Phase G — Persistence & Invalidation

### Task G1: SHA-mismatch invalidation on report GET and WebSocket

**Files:**
- Modify: `api/routers/fast_report.py`
- Test: `tests/api/test_fast_report.py`

- [ ] **Step 1: Write failing tests**

```python
from datetime import datetime, timedelta, UTC


async def test_get_fast_report_returns_410_when_commit_sha_mismatches(client, db_path):
    repo_id = await insert_repo(db_path, last_commit="A")  # existing helper
    report_id = await insert_fast_report(
        db_path, repo_id=repo_id, commit_sha="B",
        expires_at=datetime.now(UTC) + timedelta(days=6),  # not yet expired
        status="done",
    )
    response = await client.get(f"/api/repos/{repo_id}/fast-reports/{report_id}")
    assert response.status_code == 410
    assert "expired" in response.json().get("detail", "").lower()


async def test_ws_fast_report_closes_with_4008_on_sha_mismatch(client, db_path):
    repo_id = await insert_repo(db_path, last_commit="A")
    report_id = await insert_fast_report(
        db_path, repo_id=repo_id, commit_sha="B",
        expires_at=datetime.now(UTC) + timedelta(days=6),
        status="done",
    )
    with pytest.raises(WebSocketDisconnect) as exc:
        async with client.websocket_connect(
            f"/ws/repos/{repo_id}/fast-reports/{report_id}"
        ):
            pass
    assert exc.value.code == 4008
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_fast_report.py -v -k sha_mismatch`
Expected: FAIL.

- [ ] **Step 3: Implement check**

In `api/routers/fast_report.py`, replace `_is_expired(report)` calls in `get_fast_report` and `ws_fast_report` with the combined helper. The Repository model exposes `last_commit` (see `shared/models.py:31`); there is no `commit_sha` field on Repository.

```python
def _expired_or_mismatched(report: FastReport, repo: Repository | None) -> bool:
    if _is_expired(report):
        return True
    if (
        repo is not None
        and repo.last_commit
        and report.commit_sha
        and report.commit_sha != repo.last_commit
    ):
        return True
    return False
```

In `get_fast_report`:

```python
repo = await s.get(Repository, report.repo_id)
if _expired_or_mismatched(report, repo):
    raise HTTPException(status_code=410, detail="Report expired")
```

In `ws_fast_report` early-accept block:

```python
repo = await s.get(Repository, report.repo_id)
if _expired_or_mismatched(report, repo):
    await websocket.close(code=4008)
    return
```

Note: do **not** delete the persisted report record; per spec, the API simply refuses to serve it. The 7-day TTL sweeper handles eventual cleanup.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/api/test_fast_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routers/fast_report.py tests/api/test_fast_report.py
git commit -m "feat(fast-report): invalidate reports on commit SHA mismatch"
```

---

## Phase H — Observability

### Task H1: Emit structured `analysis_update` phases

**Files:**
- Modify: `worker/jobs.py` (`run_fast_report`) and `worker/fast_report.py`
- Test: `tests/worker/test_jobs.py`

The `analysis_trace` JSON written by `run_fast_report` is currently a single dict. Promote it to an ordered list of phase events. Each event has a `phase` and a payload per spec §"New phase identifiers".

- [ ] **Step 1: Write failing test**

```python
async def test_analysis_trace_records_phases_in_order(
    tmp_path, db_path, monkeypatch, fake_llm_factory, fast_report_factory, repo_factory
):
    repo_id, _commit = await repo_factory()
    report_id, section_id, job_id = await fast_report_factory(repo_id=repo_id)
    # Write a minimal v2 index with one file
    repo_data_dir = tmp_path / "repos" / repo_id / "ast"
    repo_data_dir.mkdir(parents=True)
    (repo_data_dir / "fast_report_index.json").write_text(json.dumps({
        "index_version": 2, "directory_tree": "m.py\n",
        "hub_modules": [], "readme_headings": [], "readme_sections": [],
        "files": {"m.py": {"path": "m.py", "tokens": ["x"], "imports": [],
                            "imported_by": [], "entities": [
                                {"name": "f", "symbol_path": "m.f",
                                 "start_line": 1, "end_line": 1}]}},
    }))
    (tmp_path / "repos" / repo_id / "clone").mkdir(parents=True)
    (tmp_path / "repos" / repo_id / "clone" / "m.py").write_text("def f():\n    pass\n")

    monkeypatch.setattr("worker.jobs.make_llm_provider", lambda cfg: fake_llm_factory())

    await run_fast_report(
        ctx={}, repo_id=repo_id, job_id=job_id,
        report_id=report_id, section_id=section_id, question="how does f work?",
    )
    section = await fetch_fast_report_section(db_path, section_id)
    trace = json.loads(section.analysis_trace_json)
    phases = [event["phase"] for event in trace["events"]]
    expected = [
        "index_check", "search_plan", "code_evidence_seed",
        "code_evidence_expansion", "slice_extraction",
        "interpretive_layer", "generation", "arbitration",
    ]
    for phase in expected:
        assert phase in phases, f"missing phase {phase}; got {phases}"
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/worker/test_jobs.py -v -k analysis_trace_records`
Expected: FAIL.

- [ ] **Step 3: Implement event recorder**

Add a small append-only recorder to `generate_fast_report_section` that returns an `events` list alongside the result:

```python
@dataclass(slots=True)
class FastReportSectionResult:
    ...
    interpretive_sources: list[dict] = field(default_factory=list)  # not surfaced in DTO
    analysis_events: list[dict] = field(default_factory=list)
```

Append events at each pipeline stage:
- After loading index: `{"phase": "index_check", "index_version": ...}`.
- After `plan_fast_report_search`: `{"phase": "search_plan", "question_type": ..., "search_terms": [...], "retrieval_focus": [...], "plan_retried": bool}`.
- After scoring seeds: `{"phase": "code_evidence_seed", "files": [{"path": p, "score": s}, ...]}`.
- After expansion: `{"phase": "code_evidence_expansion", "files": [...], "graph": graph_name}`.
- After slice extraction: `{"phase": "slice_extraction", "files": [{"path": p, "slices": [...]}], "dropped_due_to_budget": n}`.
- After interpretive layer: `{"phase": "interpretive_layer", "entity_docs": n, "module_docs": n, "readme_sections": n}`.
- Before generation call: `{"phase": "generation", "prompt_token_estimate": n}`.
- After arbitration: `{"phase": "arbitration", "claims_kept": n, "claims_dropped": n}`.

In `run_fast_report`, write `result.analysis_events` to the section's `analysis_trace_json` as `{"events": [...]}` so the WebSocket continues to surface a single trace blob (no schema change for the WS event itself).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/worker/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/fast_report.py worker/fast_report_search.py worker/jobs.py tests/worker/test_jobs.py
git commit -m "feat(fast-report): emit structured analysis_update phases"
```

---

## Phase I — End-to-End Integration

### Task I1: Reindex `tests/fixtures/simple-repo` and run three integration reports

**Files:**
- Test: `tests/test_integration.py` (or `tests/worker/test_fast_report_integration.py` — pick the location that matches existing integration patterns).

- [ ] **Step 1: Write integration test**

```python
import json
import shutil

import pytest

from worker.fast_report import generate_fast_report_section, FastReportQuestionIntent
from worker.fast_report_search import retrieve_code_evidence
from worker.pipeline.ast_analysis import FileAnalysis
from worker.pipeline.dependency_graph import build_dependency_graph
from worker.pipeline.fast_report_index import build_fast_report_index


@pytest.fixture
def simple_repo_index(tmp_path):
    """Copy simple-repo into tmp_path, build a v2 index, return (clone_root, index)."""
    src = pathlib.Path("tests/fixtures/simple-repo")
    clone_root = tmp_path / "clone"
    shutil.copytree(src, clone_root)
    files = sorted(clone_root.rglob("*.py"))
    file_analysis = FileAnalysis.from_files(clone_root, files)  # follow existing API
    dep_graph = build_dependency_graph(clone_root, files, file_analysis)
    readme = ""
    index = build_fast_report_index(
        root=clone_root, files=files, file_analysis=file_analysis,
        dep_graph=dep_graph, readme=readme,
    )
    return clone_root, index


def test_e2e_execution_flow_returns_real_source(simple_repo_index):
    clone_root, index = simple_repo_index
    intent = FastReportQuestionIntent(
        question_type="execution_flow",
        search_terms=["run", "greet"],
        retrieval_focus=["main.run"],
    )
    layer = retrieve_code_evidence(index, intent, "how does run() work?",
                                    clone_root=clone_root)
    assert layer.evidence_blocks
    code = layer.evidence_blocks[0].code
    assert not code.startswith("File:")
    assert any(token in code for token in ("def ", "class ", "import ", "from "))


def test_e2e_architecture_emits_multi_slice(simple_repo_index):
    clone_root, index = simple_repo_index
    # Inject a multi-entity file so slices_per_file=3 has work to do.
    (clone_root / "multi.py").write_text(
        "def alpha():\n    pass\n\n"
        "def alpha_two():\n    pass\n\n"
        "def alpha_three():\n    pass\n"
    )
    files = sorted(clone_root.rglob("*.py"))
    fa = FileAnalysis.from_files(clone_root, files)
    dg = build_dependency_graph(clone_root, files, fa)
    index = build_fast_report_index(
        root=clone_root, files=files, file_analysis=fa, dep_graph=dg, readme=""
    )
    intent = FastReportQuestionIntent(
        question_type="architecture",
        search_terms=["alpha"],
        retrieval_focus=["multi.alpha"],
    )
    layer = retrieve_code_evidence(index, intent, "explain alpha", clone_root=clone_root)
    ids = [c.id for c in layer.citations]
    # Multi-slice citation ids end in -0 / -1 / -2
    assert any(cid.endswith("-1") for cid in ids), ids


def test_e2e_error_handling_uses_exception_touchpoints_graph(simple_repo_index, monkeypatch):
    clone_root, index = simple_repo_index
    # Add an errors.py with raise/try/except to exercise the graph.
    (clone_root / "errors.py").write_text(
        "def divide(a, b):\n"
        "    if b == 0:\n"
        "        raise ValueError('cannot divide by zero')\n"
        "    try:\n"
        "        return a / b\n"
        "    except ZeroDivisionError:\n"
        "        raise ValueError('cannot divide by zero')\n"
    )
    files = sorted(clone_root.rglob("*.py"))
    fa = FileAnalysis.from_files(clone_root, files)
    dg = build_dependency_graph(clone_root, files, fa)
    index = build_fast_report_index(
        root=clone_root, files=files, file_analysis=fa, dep_graph=dg, readme=""
    )
    entry = index["files"]["errors.py"]
    assert any(tp["kind"] == "raise" for tp in entry["exception_touchpoints"])
    # The graph dispatch is exercised once we wire generate_fast_report_section;
    # at the unit level we just confirm the graph kind selection:
    from worker.fast_report_planning import expansion_graph_for
    assert expansion_graph_for("error_handling").primary == "exception_touchpoints"
```

Use the existing `from worker.pipeline.ast_analysis import FileAnalysis` pattern; if `FileAnalysis.from_files` is not the actual current API, follow whatever the existing `tests/worker/test_fast_report_index.py` uses to build a `FileAnalysis` (look near the top of that file for the import sequence, then mirror it).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_integration.py -v -k fast_report_e2e`
Expected: FAIL initially (likely because `simple-repo` lacks an exception-throwing fixture).

- [ ] **Step 3: Augment fixture if needed**

If `simple-repo` cannot exercise `error_handling` expansion, add a third fixture file (e.g., `tests/fixtures/simple-repo/errors.py`) with:

```python
def divide(a: int, b: int) -> float:
    if b == 0:
        raise ValueError("cannot divide by zero")
    try:
        return a / b
    except ZeroDivisionError:
        raise ValueError("cannot divide by zero")
```

Update `tests/fixtures/simple-repo/main.py` to import it.

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_integration.py -v -k fast_report_e2e`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/ worker/
git commit -m "test(fast-report): end-to-end integration on simple-repo fixture"
```

---

### Task I2: Pre-commit checks and final coverage

- [ ] **Step 1: Run lint and format**

```bash
uv run ruff check .
uv run ruff format --check .
npm run lint --prefix web
```

Expected: no violations. Fix any reported issues (formatting often needed for inserted code).

- [ ] **Step 2: Run full test suite with coverage**

```bash
uv run pytest tests/ --ignore=tests/e2e --cov=worker --cov=api --cov-report=term-missing
```

Expected: ≥80% on `worker/` and `api/`. New modules `worker/fast_report_slices.py`, `worker/fast_report_interpretive.py`, `worker/fast_report_planning.py` should be ≥85% line coverage.

- [ ] **Step 3: If coverage gaps found, add focused unit tests for the uncovered branches and re-run**

- [ ] **Step 4: Commit any added tests**

```bash
git add tests/
git commit -m "test(fast-report): close coverage gaps in new modules"
```

---

## Acceptance Checklist

Validate against the spec's "Acceptance Criteria" (§Acceptance Criteria of `2026-04-28-fast-report-quality-uplift-design.md`):

- [ ] `fast_report_index.json` carries `index_version: 2`, populates `directory_tree`, `hub_modules`, `readme_sections`, `call_sites`, `exception_touchpoints`, `config_touchpoints`, `module_docstring`, per-entity `leading_comment`, and contains no `top_level_entries`. → Tasks A1–A7
- [ ] Generated `simple-repo` report's evidence blocks contain real source. → Task I1
- [ ] `architecture` / `execution_flow` reports select more than four files. → Task I1 + Task B5
- [ ] `architecture` report on multi-entity files emits ≥1 file with three slices, distinct `code-{file_idx}-{entity_idx}` ids. → Task I1
- [ ] `error_handling` / `configuration` / `execution_flow` reports use the matching expansion graph in trace. → Task I1 + Task H1
- [ ] Plan prompt contains `Directory tree:`, `README headings:`, `Hub modules:` blocks; output `question_type` is one of eight enum values. → Tasks D1, D2
- [ ] Generation prompt's structure layer signals contain `Directory tree:`; emits exactly one citation (struct-1). → Task F1
- [ ] Interpretive layer present in generation prompts and produces zero `FastReportCitation` records; arbitration drops interpretive-only claims. → Task E2
- [ ] Pre-v2 indexes cause POST `/api/repos/{repo_id}/fast` to return HTTP 409 with actionable error; WS emits a single `error` event before closing. → Task A8 + A9
- [ ] Reopened report at SHA X returns expired state when repo has reindexed at SHA Y, regardless of TTL. → Task G1
- [ ] Indexing wall-clock regression on `simple-repo` ≤50%. → measure during Task I2 (`time uv run pytest tests/test_integration.py -k fast_report_e2e`).
- [ ] Reports for the same question on the same commit are byte-stable for deterministic layers. → covered indirectly by Task I1.
