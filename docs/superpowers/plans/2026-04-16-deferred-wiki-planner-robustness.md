# Deferred Planner Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the three items explicitly deferred from `docs/superpowers/plans/2026-04-15-wiki-planner-robustness.md` — (1) Layer C1 outline anchors, (2) Layer C2 multi-page file assignment, and (3) the independent stage validation harness — so the Phase 1/2 planner produces less-fragmented page hierarchies, one file can surface on multiple pages when it genuinely belongs to both, and maintainers can diagnose planner output without burning live API budget.

**Architecture:** Three independently shippable stages. Stage A enriches `_build_outline_prompt` with a directory tree, package docstrings, and README subsystem headings via a new `worker/pipeline/outline_anchors.py` module. Stage B evolves the assignment schema + `WikiPageSpec` to carry `secondary_files`, and threads the distinction through validation, incremental refresh, and page generation context. Stage C adds a fixture recorder to the live pipeline and a `autowiki validate-plan` CLI that replays the planner stages against the recorded fixtures and reports coverage/size/validation statistics.

**Tech Stack:** Python 3.12, asyncio, pytest (`asyncio_mode=auto`), Typer, Anthropic/OpenAI/Gemini/Ollama SDKs.

**Spec:** This plan. Backreferences:
- Parent plan: `docs/superpowers/plans/2026-04-15-wiki-planner-robustness.md` (Out of scope section + Task 13).
- Implementation notes: `CLAUDE.md` → "Deferred Planner Improvements".

**Not in scope:**
- Hybrid-search / GitLab / Bitbucket / MCP (Phase 5 — tracked separately).
- Changing the 4-pass page-generation orchestrator (Phase 2.5 is settled).
- Live-API e2e validation (Stage C covers offline replay; a live smoke test is out of budget).

---

## File Structure

**Stage A — Outline Anchors**

Created:
- `worker/pipeline/outline_anchors.py` — pure-function helpers that synthesise directory-tree / package-docstring / README-section anchors from existing pipeline artefacts.
- `tests/worker/test_outline_anchors.py` — unit tests for each helper.

Modified:
- `worker/pipeline/wiki_planner.py` — `_build_outline_prompt` accepts new anchor inputs; `generate_wiki_plan` threads `clone_root` + `file_analysis` through.
- `worker/jobs.py` — call sites pass `clone_root`.
- `CLAUDE.md` — document the anchor semantics under "Key Implementation Notes".

**Stage B — Multi-page File Assignment**

Modified:
- `worker/pipeline/wiki_planner.py` — `_ASSIGNMENT_SCHEMA`, `_build_batch_assignment_user`, `_assign_files_in_batches`, `_validate_assignments`, `WikiPageSpec`, `WikiPlan`, `validate_wiki_plan`.
- `worker/pipeline/ingestion.py` — `get_affected_pages` returns a richer structure.
- `worker/pipeline/page_generator.py` — context builder includes secondary files as "referenced" material.
- `worker/jobs.py` — incremental refresh uses the new affected-pages result.
- `CLAUDE.md` — document the schema + refresh semantics.

Tests touched:
- `tests/worker/test_wiki_planner.py`
- `tests/worker/test_assign_files_batched.py`
- `tests/worker/test_ingestion.py`
- `tests/worker/test_page_generator.py` (create if missing)
- `tests/worker/test_jobs.py`

**Stage C — Stage Validation Harness**

Created:
- `worker/pipeline/fixture_recorder.py` — helpers to dump `outline.json`, `assignments.json`, and `wiki_plan.json`.
- `cli/commands/validate_plan.py` — `autowiki validate-plan <repo>` Typer command.
- `tests/worker/test_fixture_recorder.py`
- `tests/cli/test_validate_plan.py`

Modified:
- `worker/jobs.py` — call the recorder during full index + refresh when a debug flag or env var is set.
- `cli/main.py` — register the new command.
- `CLAUDE.md` — document the debug env var and the CLI surface.

---

## Stage A — Outline Anchors (Layer C1)

**Objective:** Provide the Phase-1 LLM with three explicit architectural signals so cohesive subsystems (e.g. `worker/pipeline/*`) stop being shattered across peer top-level pages:

1. **Top-3-level directory tree with file counts** — a compact ASCII tree over the repo's directory structure limited to three depth levels.
2. **Package docstrings** — leading module docstrings from `__init__.py` (Python), `mod.rs` (Rust), and `index.ts`/`index.js` (JS/TS). Up to 25 strongest-signal entries.
3. **README subsystem headings** — `##`/`###` Markdown headings extracted from the README.

These anchors go into a new "Architectural anchors" prompt section sourced from a pure helper module. `_build_outline_prompt` gains three new optional args; behaviour without them is identical to today. `generate_wiki_plan` + `worker/jobs.py` feed them in.

### Task A1: Create the `outline_anchors` module with helpers + tests

**Files:**
- Create: `worker/pipeline/outline_anchors.py`
- Create: `tests/worker/test_outline_anchors.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/worker/test_outline_anchors.py`:

```python
"""Tests for the outline-anchor helpers (Layer C1)."""

from __future__ import annotations

from pathlib import Path

from worker.pipeline.outline_anchors import (
    build_directory_tree,
    extract_package_docstrings,
    extract_readme_sections,
    format_anchors_for_prompt,
)


def test_build_directory_tree_counts_and_depth():
    files = [
        "worker/pipeline/wiki_planner.py",
        "worker/pipeline/page_generator.py",
        "worker/pipeline/ast_analysis.py",
        "worker/jobs.py",
        "worker/llm/base.py",
        "worker/llm/anthropic_provider.py",
        "api/routers/repos.py",
        "api/routers/wiki.py",
        "api/main.py",
        "web/app/page.tsx",
        "README.md",
    ]
    tree = build_directory_tree(files, max_depth=3)
    # Top-level dirs and their subtree file counts
    assert "worker/ (6)" in tree
    assert "api/ (3)" in tree
    assert "web/ (1)" in tree
    # Root-level files listed under a synthetic "(root)" bucket
    assert "(root) (1)" in tree
    # Depth-2 sub-directories appear with counts
    assert "pipeline/ (3)" in tree
    assert "routers/ (2)" in tree
    # Depth-4 files are NOT expanded individually (max_depth=3)
    assert "wiki_planner.py" not in tree


def test_build_directory_tree_is_stable_ordered():
    """Output order is deterministic (alphabetical at each level)."""
    files = ["z/a.py", "a/z.py", "a/a.py", "m/b.py"]
    tree = build_directory_tree(files, max_depth=3)
    a_idx = tree.index("a/")
    m_idx = tree.index("m/")
    z_idx = tree.index("z/")
    assert a_idx < m_idx < z_idx


def test_extract_package_docstrings_python_init(tmp_path: Path):
    pkg = tmp_path / "worker" / "pipeline"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        '"""Pipeline stages for the wiki generator.\n\n'
        "Exports the public entry points used by worker/jobs.py.\n"
        '"""\n'
        "from .wiki_planner import generate_wiki_plan\n"
    )
    # Non-package file that should NOT be picked up
    (tmp_path / "worker" / "jobs.py").write_text("'just a module'\nx = 1\n")

    result = extract_package_docstrings(
        clone_root=tmp_path,
        rel_paths=["worker/pipeline/__init__.py", "worker/jobs.py"],
        max_entries=10,
    )
    # Only the __init__.py is surfaced
    assert len(result) == 1
    assert result[0].package == "worker/pipeline"
    assert "Pipeline stages" in result[0].docstring
    # Docstring is trimmed (no triple quotes)
    assert '"""' not in result[0].docstring


def test_extract_package_docstrings_rust_and_ts(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.rs").write_text(
        "//! Graph-index helpers.\n"
        "//! Used by the ingest pipeline.\n"
        "pub fn build() {}\n"
    )
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "index.ts").write_text(
        "/**\n * UI kit barrel entrypoint.\n */\n"
        "export { Button } from './button';\n"
    )
    result = extract_package_docstrings(
        clone_root=tmp_path,
        rel_paths=["src/mod.rs", "ui/index.ts"],
        max_entries=10,
    )
    packages = {r.package: r.docstring for r in result}
    assert "Graph-index helpers" in packages["src"]
    assert "UI kit barrel" in packages["ui"]


def test_extract_package_docstrings_caps_results(tmp_path: Path):
    for i in range(30):
        pkg = tmp_path / f"pkg{i:02d}"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(f'"""Package {i}."""\n')
    rels = [f"pkg{i:02d}/__init__.py" for i in range(30)]
    result = extract_package_docstrings(
        clone_root=tmp_path, rel_paths=rels, max_entries=5
    )
    assert len(result) == 5


def test_extract_readme_sections_returns_h2_and_h3():
    readme = (
        "# My Project\n\n"
        "Intro paragraph.\n\n"
        "## Architecture\n\n"
        "Some text.\n\n"
        "### Components\n\n"
        "More text.\n\n"
        "### Data Flow\n\n"
        "## Installation\n\n"
        "```\n"
        "## not a heading (inside code fence)\n"
        "```\n"
        "## Development\n"
    )
    sections = extract_readme_sections(readme)
    assert sections == [
        ("##", "Architecture"),
        ("###", "Components"),
        ("###", "Data Flow"),
        ("##", "Installation"),
        ("##", "Development"),
    ]


def test_extract_readme_sections_none_returns_empty():
    assert extract_readme_sections(None) == []
    assert extract_readme_sections("") == []


def test_format_anchors_for_prompt_omits_empty_sections():
    out = format_anchors_for_prompt(
        directory_tree="",
        package_docstrings=[],
        readme_sections=[],
    )
    assert out == ""


def test_format_anchors_for_prompt_includes_each_section():
    from worker.pipeline.outline_anchors import PackageDoc

    out = format_anchors_for_prompt(
        directory_tree="worker/ (3)\n  pipeline/ (2)",
        package_docstrings=[
            PackageDoc(package="worker/pipeline", docstring="Pipeline stages.")
        ],
        readme_sections=[("##", "Architecture"), ("###", "Components")],
    )
    assert "## Directory layout" in out
    assert "worker/ (3)" in out
    assert "## Package docstrings" in out
    assert "worker/pipeline: Pipeline stages." in out
    assert "## README sections" in out
    assert "## Architecture" in out
    assert "### Components" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/worker/test_outline_anchors.py -v
```

Expected: ImportError — module does not exist yet.

- [ ] **Step 3: Implement the helpers**

Create `worker/pipeline/outline_anchors.py`:

```python
"""Architectural anchor helpers for the Phase-1 wiki outline prompt.

The wiki planner's Phase-1 LLM call tends to fragment cohesive
subsystems when the only signal it receives is the flat file summary.
The helpers in this module surface three complementary signals that are
cheap to compute and strongly steer the outline toward the real
architecture:

1. :func:`build_directory_tree` — a compact ASCII tree of the repo's
   directory structure (up to three levels deep) with subtree file
   counts.
2. :func:`extract_package_docstrings` — leading module docstrings from
   package-entry files (Python ``__init__.py``, Rust ``mod.rs`` /
   ``lib.rs``, JS/TS ``index.ts`` / ``index.js``) which usually describe
   the subsystem in one or two sentences.
3. :func:`extract_readme_sections` — the ``##`` / ``###`` Markdown
   headings of the repo's README, which are the author's own mental
   model of the project's layout.

:func:`format_anchors_for_prompt` stitches the three into a Markdown
block ready to drop into :func:`worker.pipeline.wiki_planner._build_outline_prompt`.

All helpers are **pure** — they take plain data and return plain
data.  No I/O except :func:`extract_package_docstrings`, which reads
files through an explicit ``clone_root`` argument so tests can run
against a ``tmp_path``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PackageDoc:
    """A package-entry file's leading docstring.

    ``package`` is the POSIX path of the package directory
    (e.g. ``"worker/pipeline"``).  ``docstring`` is the trimmed
    leading docstring, ≤ 500 chars.
    """

    package: str
    docstring: str


# ── Directory tree ───────────────────────────────────────────────────────

def build_directory_tree(files: list[str], max_depth: int = 3) -> str:
    """Render a compact ASCII tree of *files* up to *max_depth* levels.

    Each directory line carries the count of descendant files in
    parentheses.  Files at the repo root are grouped into a synthetic
    ``(root)`` bucket so the output is a single rooted tree.  Ordering
    is alphabetical at every level so the output is deterministic and
    diff-friendly.

    Example output::

        (root) (1)
        api/ (3)
          main.py
          routers/ (2)
        worker/ (6)
          jobs.py
          llm/ (2)
          pipeline/ (3)
    """
    if not files:
        return ""

    # Build a nested dict: {dir: {file|subdir: ...}}
    root: dict[str, dict | None] = {}
    for rel in files:
        parts = rel.split("/")
        node = root
        for i, part in enumerate(parts):
            is_file = i == len(parts) - 1
            if is_file:
                node[part] = None
            else:
                node = node.setdefault(part + "/", {})

    def _count(node: dict[str, dict | None]) -> int:
        total = 0
        for key, child in node.items():
            total += 1 if child is None else _count(child)
        return total

    lines: list[str] = []

    def _emit(node: dict[str, dict | None], depth: int, prefix: str) -> None:
        if depth > max_depth:
            return
        # Directories first (alphabetical), then files.
        dirs = sorted(k for k, v in node.items() if isinstance(v, dict))
        files_here = sorted(k for k, v in node.items() if v is None)

        # Synthetic (root) bucket for top-level files only at depth 1.
        if depth == 1 and files_here:
            lines.append(f"(root) ({len(files_here)})")

        for d in dirs:
            sub = node[d]
            assert isinstance(sub, dict)
            lines.append(f"{prefix}{d} ({_count(sub)})")
            _emit(sub, depth + 1, prefix + "  ")

    _emit(root, 1, "")
    return "\n".join(lines)


# ── Package docstrings ───────────────────────────────────────────────────

_PACKAGE_ENTRY_FILENAMES = ("__init__.py", "mod.rs", "lib.rs", "index.ts", "index.js")

_PY_DOCSTRING_RE = re.compile(r'^\s*(?P<q>"""|\'\'\')(?P<body>.*?)(?P=q)', re.DOTALL)
_RUST_DOC_LINE_RE = re.compile(r"^\s*//!\s?(.*)$")
_JS_BLOCK_DOC_RE = re.compile(r"^\s*/\*\*(?P<body>.*?)\*/", re.DOTALL)


def _extract_python_docstring(text: str) -> str | None:
    m = _PY_DOCSTRING_RE.match(text.lstrip())
    return m.group("body").strip() if m else None


def _extract_rust_doc(text: str) -> str | None:
    lines: list[str] = []
    for raw in text.splitlines():
        m = _RUST_DOC_LINE_RE.match(raw)
        if m:
            lines.append(m.group(1))
        elif lines:
            # Doc block ends at the first non-doc line
            break
    return "\n".join(lines).strip() or None


def _extract_js_doc(text: str) -> str | None:
    m = _JS_BLOCK_DOC_RE.match(text.lstrip())
    if not m:
        return None
    body = m.group("body")
    # Strip leading " * " from each line
    cleaned = "\n".join(
        re.sub(r"^\s*\*\s?", "", ln).rstrip() for ln in body.splitlines()
    ).strip()
    return cleaned or None


def extract_package_docstrings(
    clone_root: Path,
    rel_paths: list[str],
    max_entries: int = 25,
    max_chars: int = 500,
) -> list[PackageDoc]:
    """Return up to *max_entries* package-entry docstrings.

    A path qualifies as a package entry when its basename is one of
    :data:`_PACKAGE_ENTRY_FILENAMES`.  Files are visited in *rel_paths*
    order so callers can pass an importance-ranked list to bias the
    selection.  Each docstring is trimmed to *max_chars* characters and
    the ``package`` field is the POSIX path of the entry file's parent
    directory.
    """
    results: list[PackageDoc] = []
    for rel in rel_paths:
        if len(results) >= max_entries:
            break
        basename = rel.rsplit("/", 1)[-1]
        if basename not in _PACKAGE_ENTRY_FILENAMES:
            continue
        path = clone_root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if basename == "__init__.py":
            doc = _extract_python_docstring(text)
        elif basename in ("mod.rs", "lib.rs"):
            doc = _extract_rust_doc(text)
        else:  # index.ts / index.js
            doc = _extract_js_doc(text)

        if not doc:
            continue
        if len(doc) > max_chars:
            doc = doc[:max_chars].rstrip() + "..."
        package = rel.rsplit("/", 1)[0] if "/" in rel else "."
        results.append(PackageDoc(package=package, docstring=doc))
    return results


# ── README sections ──────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^```")


def extract_readme_sections(readme: str | None) -> list[tuple[str, str]]:
    """Return ``[(level, title)]`` for every ``##`` / ``###`` heading.

    Headings inside fenced code blocks are ignored so the extractor
    stays robust against READMEs that happen to embed raw Markdown in a
    code fence.  Trailing closing ``#`` characters (ATX-style) are
    stripped.
    """
    if not readme:
        return []
    result: list[tuple[str, str]] = []
    in_fence = False
    for line in readme.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            result.append((m.group(1), m.group(2).strip()))
    return result


# ── Prompt formatter ─────────────────────────────────────────────────────

def format_anchors_for_prompt(
    directory_tree: str,
    package_docstrings: list[PackageDoc],
    readme_sections: list[tuple[str, str]],
) -> str:
    """Combine the three anchor outputs into a single Markdown block.

    Returns an empty string when all three inputs are empty so callers
    can unconditionally concatenate the output.
    """
    sections: list[str] = []
    if directory_tree:
        sections.append("## Directory layout\n" + directory_tree)
    if package_docstrings:
        lines = [
            f"{p.package}: {p.docstring}" for p in package_docstrings
        ]
        sections.append("## Package docstrings\n" + "\n".join(lines))
    if readme_sections:
        lines = [f"{lvl} {title}" for lvl, title in readme_sections]
        sections.append("## README sections\n" + "\n".join(lines))
    return "\n\n".join(sections)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/worker/test_outline_anchors.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/outline_anchors.py tests/worker/test_outline_anchors.py
git commit -m "feat(planner): add outline-anchor helpers (directory tree, package docs, README sections)"
```

---

### Task A2: Wire anchors into `_build_outline_prompt`

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — extend `_build_outline_prompt` signature + call sites in `_generate_outline`/`generate_wiki_plan`
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/worker/test_wiki_planner.py`:

```python
def test_build_outline_prompt_includes_anchors_section_when_provided():
    """When anchors are passed in, the prompt must surface them under a
    dedicated heading, not bury them in the existing sections."""
    from worker.pipeline.outline_anchors import PackageDoc
    from worker.pipeline.wiki_planner import _build_outline_prompt

    prompt = _build_outline_prompt(
        file_summary="one.py, two.py",
        repo_name="demo",
        anchors_block=(
            "## Directory layout\nworker/ (3)\n"
            "\n## Package docstrings\nworker: core pipeline."
        ),
    )
    assert "Architectural anchors" in prompt
    assert "worker/ (3)" in prompt
    assert "worker: core pipeline." in prompt
    # Still contains the existing guidance
    assert "Create a hierarchical wiki plan." in prompt


def test_build_outline_prompt_without_anchors_unchanged():
    """Call sites that do not pass anchors must see byte-identical output
    to the pre-anchor behaviour (modulo already-existing sections)."""
    from worker.pipeline.wiki_planner import _build_outline_prompt

    prompt = _build_outline_prompt(
        file_summary="one.py, two.py",
        repo_name="demo",
    )
    assert "Architectural anchors" not in prompt
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_build_outline_prompt_includes_anchors_section_when_provided tests/worker/test_wiki_planner.py::test_build_outline_prompt_without_anchors_unchanged -v
```

Expected: first FAIL (no `anchors_block` kwarg); second might pass depending on current signature.

- [ ] **Step 3: Extend `_build_outline_prompt`**

Modify the signature and body of `_build_outline_prompt` in `worker/pipeline/wiki_planner.py` (current lines 329-394). Change the signature to:

```python
def _build_outline_prompt(
    file_summary: str,
    repo_name: str,
    readme: str | None = None,
    dep_info: str | None = None,
    clusters: list[list[str]] | None = None,
    page_range: tuple[int, int] = (5, 20),
    anchors_block: str | None = None,
) -> str:
```

Insert the anchors section between the README (if present) and the file-summary section. After the `if readme:` block append:

```python
    if anchors_block:
        sections.append("Architectural anchors:\n" + anchors_block)
```

Leave the rest of the function untouched.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_build_outline_prompt_includes_anchors_section_when_provided tests/worker/test_wiki_planner.py::test_build_outline_prompt_without_anchors_unchanged -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): accept anchors_block in _build_outline_prompt"
```

---

### Task A3: Thread anchors through `_generate_outline` and `generate_wiki_plan`

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — `_generate_outline` + `generate_wiki_plan`
- Modify: `worker/jobs.py` — pass `clone_root` when calling `generate_wiki_plan`
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing integration test**

Add to `tests/worker/test_wiki_planner.py`:

```python
async def test_generate_wiki_plan_passes_anchors_into_outline_prompt(
    tmp_path, monkeypatch
):
    """generate_wiki_plan must build anchors from clone_root + file_analysis
    and include them in the Phase-1 prompt."""
    from unittest.mock import AsyncMock

    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
    from worker.pipeline.wiki_planner import generate_wiki_plan

    # Fixture clone with a package-entry docstring to surface
    pkg = tmp_path / "worker" / "pipeline"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""Pipeline stages for the demo repo."""\n')
    (pkg / "wiki_planner.py").write_text("def plan(): pass\n")

    file_info = {
        "worker/pipeline/__init__.py": FileInfo(
            rel_path="worker/pipeline/__init__.py"
        ),
        "worker/pipeline/wiki_planner.py": FileInfo(
            rel_path="worker/pipeline/wiki_planner.py",
            entities=[{"type": "function", "name": "plan"}],
        ),
    }
    analysis = FileAnalysis(files=file_info)

    captured_prompts: list[str] = []

    async def fake_generate_structured(prompt, schema=None, system=None):
        captured_prompts.append(prompt if isinstance(prompt, str) else str(prompt))
        if schema == _OUTLINE_SCHEMA_SENTINEL:
            return {
                "pages": [
                    {"title": "Overview", "purpose": "top"},
                    {"title": "Pipeline", "purpose": "stages", "parent": "Overview"},
                ]
            }
        # Phase-2 assignment — return a single bundle for the Pipeline page
        return {
            "assignments": [
                {"file": f, "page_title": "Pipeline"}
                for f in analysis.files
            ]
        }

    # Expose the outline schema sentinel for routing
    from worker.pipeline import wiki_planner as wp
    _OUTLINE_SCHEMA_SENTINEL = wp._OUTLINE_SCHEMA

    llm = AsyncMock()
    llm.generate_structured.side_effect = fake_generate_structured

    await generate_wiki_plan(
        file_analysis=analysis,
        repo_name="demo",
        llm=llm,
        clone_root=tmp_path,
    )

    # The first LLM call is the Phase-1 outline; its prompt must include
    # the anchors section and the package docstring.
    assert captured_prompts, "no LLM call was made"
    outline_prompt = captured_prompts[0]
    assert "Architectural anchors" in outline_prompt
    assert "Pipeline stages for the demo repo." in outline_prompt
    assert "worker/pipeline" in outline_prompt
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_generate_wiki_plan_passes_anchors_into_outline_prompt -v
```

Expected: FAIL — `generate_wiki_plan` does not accept `clone_root`.

- [ ] **Step 3: Add `clone_root` to `generate_wiki_plan` and build anchors**

In `worker/pipeline/wiki_planner.py`, update the import block to include:

```python
from worker.pipeline.outline_anchors import (
    build_directory_tree,
    extract_package_docstrings,
    extract_readme_sections,
    format_anchors_for_prompt,
)
```

Add `clone_root: Path | None = None` to `generate_wiki_plan`'s signature (alongside the other optional kwargs) and add the import `from pathlib import Path` at the top if not already present.

Then inside `generate_wiki_plan`, before the existing `# Phase 1` block, build the anchors:

```python
    anchors_block: str | None = None
    if clone_root is not None:
        # Rank files by importance so the most central packages surface first.
        from worker.pipeline.ast_analysis import _rank_files_by_importance

        ranked = _rank_files_by_importance(
            list(file_analysis.files),
            file_analysis.files,
            dep_graph=dep_graph,
        )
        tree = build_directory_tree(list(file_analysis.files), max_depth=3)
        pkg_docs = extract_package_docstrings(
            clone_root=clone_root,
            rel_paths=ranked,
            max_entries=25,
        )
        readme_sections = extract_readme_sections(readme)
        anchors_block = format_anchors_for_prompt(
            directory_tree=tree,
            package_docstrings=pkg_docs,
            readme_sections=readme_sections,
        ) or None
```

Then update the `_generate_outline(...)` call to pass `anchors_block=anchors_block`. Update `_generate_outline`'s signature to accept `anchors_block: str | None = None` and forward it:

```python
    prompt = _build_outline_prompt(
        file_summary=file_summary,
        repo_name=repo_name,
        readme=readme,
        dep_info=dep_info,
        clusters=clusters,
        page_range=page_range,
        anchors_block=anchors_block,
    )
```

- [ ] **Step 4: Update the worker call site in `worker/jobs.py`**

Search `worker/jobs.py` for `generate_wiki_plan(` calls (there is one in full-index and one in incremental refresh). Add `clone_root=clone_root` to both calls. Use Grep to locate them:

```bash
grep -n "generate_wiki_plan(" worker/jobs.py
```

Expected locations: full-index pipeline stage 5 and the refresh path. Edit both to pass `clone_root=clone_root` (the variable is already in scope — it is the argument that ran the clone step).

- [ ] **Step 5: Run the test + the existing planner tests**

```bash
uv run pytest tests/worker/test_wiki_planner.py tests/worker/test_outline_anchors.py -v
```

Expected: all pass. If `generate_wiki_plan` existing tests break because they don't pass `clone_root`, that is fine — `clone_root` defaults to `None` and anchors are skipped.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/wiki_planner.py worker/jobs.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): build and inject architectural anchors during outline phase"
```

---

### Task A4: Lint + full-suite verification for Stage A

- [ ] **Step 1: Pre-commit checks**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: clean. If any violation, run `uv run ruff format .` / `uv run ruff check --fix .`.

- [ ] **Step 2: Full backend suite**

```bash
uv run pytest tests/ --ignore=tests/e2e -q
```

Expected: all pass.

- [ ] **Step 3: If anything fails, fix and commit before proceeding to Stage B**

---

## Stage B — Multi-page File Assignment (Layer C2)

**Objective:** Allow a single source file to appear on more than one wiki page (e.g. a shared utility referenced in both an "Overview" and a "Core Pipeline" deep-dive). The LLM returns `{file, primary_page, secondary_pages: [...≤2]}` per file. `WikiPageSpec` carries `files` (primary) and `secondary_files` (shared). Incremental refresh regenerates primary-affected pages eagerly and secondary-affected pages lazily (marked "stale" but not regenerated in the same refresh cycle). The page generator treats primary files as "owned" and secondary files as "referenced" context, concatenating their summaries into the prompt without listing them in the source-files table.

### Task B1: Evolve `WikiPageSpec` and `WikiPlan` serialisation

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — `WikiPageSpec`, `WikiPlan.to_*`
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/worker/test_wiki_planner.py`:

```python
def test_wiki_page_spec_has_secondary_files():
    """WikiPageSpec must carry an optional secondary_files list."""
    from worker.pipeline.wiki_planner import WikiPageSpec

    spec = WikiPageSpec(
        title="Core",
        purpose="Core",
        files=["a.py"],
        secondary_files=["shared/utils.py"],
    )
    assert spec.files == ["a.py"]
    assert spec.secondary_files == ["shared/utils.py"]


def test_wiki_page_spec_secondary_files_default_empty():
    from worker.pipeline.wiki_planner import WikiPageSpec

    spec = WikiPageSpec(title="Core", purpose="p")
    assert spec.secondary_files == []


def test_to_internal_json_roundtrips_secondary_files():
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Core",
                purpose="p",
                files=["a.py"],
                secondary_files=["b.py"],
            )
        ]
    )
    payload = plan.to_internal_json()
    page = payload["pages"][0]
    assert page["files"] == ["a.py"]
    assert page["secondary_files"] == ["b.py"]


def test_to_wiki_json_omits_secondary_files():
    """wiki.json is user-facing: secondary assignment is an implementation
    detail and must not leak into the user-editable file."""
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Core",
                purpose="p",
                files=["a.py"],
                secondary_files=["b.py"],
            )
        ]
    )
    payload = plan.to_wiki_json()
    page = payload["pages"][0]
    assert "files" not in page
    assert "secondary_files" not in page


def test_to_api_structure_exposes_secondary_file_count_only():
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Core",
                purpose="p",
                files=["a.py"],
                secondary_files=["b.py", "c.py"],
            )
        ]
    )
    page = plan.to_api_structure()["pages"][0]
    assert page["secondary_file_count"] == 2
    # Raw lists are not exposed (they are internal)
    assert "secondary_files" not in page
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/worker/test_wiki_planner.py -k "secondary" -v
```

Expected: FAIL — attribute does not exist.

- [ ] **Step 3: Add `secondary_files` to `WikiPageSpec`**

In `worker/pipeline/wiki_planner.py`, extend `WikiPageSpec` (current lines 71-140) so the dataclass has an additional field after `files`:

```python
    files: list[str] = field(default_factory=list)  # primary assignments
    secondary_files: list[str] = field(default_factory=list)
    """Files that are *referenced* by this page but *primarily owned* by
    another page.  Included in the generation prompt as "see also" context
    and used by incremental refresh to mark the page as stale when one of
    them changes."""
```

- [ ] **Step 4: Update the three `to_*` serialisers**

In `WikiPlan.to_internal_json`, emit `secondary_files` next to `files`:

```python
                {
                    "title": p.title,
                    "purpose": p.purpose,
                    "files": p.files,
                    "secondary_files": p.secondary_files,
                    **({"parent": p.parent} if p.parent is not None else {}),
                }
```

Leave `to_wiki_json` untouched — user-facing wiki.json stays free of the new field. In `to_api_structure`, add `secondary_file_count`:

```python
                {
                    "title": p.title,
                    "slug": p.slug,
                    "parent_slug": p.parent_slug,
                    "description": p.purpose,
                    "has_user_notes": _has_user_notes(p),
                    "secondary_file_count": len(p.secondary_files),
                }
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/worker/test_wiki_planner.py -k "secondary" -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): add secondary_files to WikiPageSpec + internal serialiser"
```

---

### Task B2: Evolve `_ASSIGNMENT_SCHEMA` and the batch prompts

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — `_ASSIGNMENT_SCHEMA`, `_build_batch_assignment_user`, `_assign_files_in_batches`
- Test: `tests/worker/test_assign_files_batched.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/worker/test_assign_files_batched.py`:

```python
def test_assignment_schema_has_primary_and_secondary_fields():
    from worker.pipeline.wiki_planner import _ASSIGNMENT_SCHEMA

    item = _ASSIGNMENT_SCHEMA["properties"]["assignments"]["items"]
    assert "primary_page" in item["properties"]
    assert "secondary_pages" in item["properties"]
    assert item["properties"]["secondary_pages"]["type"] == "array"
    # Only primary is required
    assert "primary_page" in item["required"]
    assert "secondary_pages" not in item["required"]
    # Secondary capped at 2
    assert item["properties"]["secondary_pages"]["maxItems"] == 2


def test_batch_user_prompt_mentions_secondary_pages():
    from worker.pipeline.wiki_planner import _build_batch_assignment_user

    seg = _build_batch_assignment_user(
        batch_files=["a.py"], outline_titles=["Core", "Overview"]
    )
    text = seg.text.lower()
    assert "primary_page" in text
    assert "secondary_pages" in text
    assert "optional" in text  # makes clear secondary is optional
    assert "at most 2" in text or "maxitems" in text or "up to 2" in text


async def test_batched_assignment_collects_secondary_assignments():
    """_assign_files_in_batches must surface secondary assignments into a
    dict[title, list[str]] keyed by page title."""
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _assign_files_in_batches

    outline = [
        {"title": "Overview", "purpose": "top"},
        {"title": "Core", "purpose": "core"},
        {"title": "Utils", "purpose": "shared"},
    ]

    async def fake(user_seg, schema, system):
        import re
        text = user_seg.text if hasattr(user_seg, "text") else str(user_seg)
        batch = re.findall(r"- (f\d+\.py)", text)
        assignments = []
        for f in batch:
            if f == "f0.py":
                assignments.append(
                    {
                        "file": f,
                        "primary_page": "Core",
                        "secondary_pages": ["Utils"],
                    }
                )
            else:
                assignments.append({"file": f, "primary_page": "Overview"})
        return {"assignments": assignments}

    llm = AsyncMock()
    llm.generate_structured.side_effect = fake

    primary, secondary = await _assign_files_in_batches(
        outline=outline,
        file_summary="fs",
        dep_info=None,
        all_files=["f0.py", "f1.py"],
        llm=llm,
        system="sys",
        on_retry=None,
        batch_size=10,
    )
    assert primary["Core"] == ["f0.py"]
    assert primary["Overview"] == ["f1.py"]
    assert primary["Utils"] == []
    assert secondary["Utils"] == ["f0.py"]
    assert secondary["Core"] == []
    assert secondary["Overview"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/worker/test_assign_files_batched.py -k "secondary or primary" -v
```

Expected: FAIL — schema/prompts/return shape unchanged.

- [ ] **Step 3: Update `_ASSIGNMENT_SCHEMA`**

In `worker/pipeline/wiki_planner.py`, replace `_ASSIGNMENT_SCHEMA` (current lines 285-301):

```python
_ASSIGNMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "primary_page": {"type": "string"},
                    "secondary_pages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 2,
                    },
                },
                "required": ["file", "primary_page"],
            },
        }
    },
    "required": ["assignments"],
}
```

- [ ] **Step 4: Update `_build_batch_assignment_user`**

Replace the prompt body in `_build_batch_assignment_user` (current lines 458-482). Specifically the rules + output description:

```python
    text = (
        f"Assign each of the following {len(batch_files)} files to the "
        f"wiki page whose purpose best matches it. Each assignment has a "
        f"required ``primary_page`` and an optional ``secondary_pages`` "
        f"list (at most 2 entries).\n\n"
        f"``primary_page`` MUST exactly match one of: {titles_str}.\n"
        f"``secondary_pages`` entries (if any) must also match one of "
        f"those titles, and must NOT equal ``primary_page``.\n\n"
        f"Files to assign:\n{files_str}\n\n"
        "Rules:\n"
        "- Every listed file must have a primary_page.\n"
        "- Use secondary_pages sparingly — only for genuinely shared "
        "utilities referenced from two or three distinct subsystems.\n"
        "- Files that import each other usually share the same primary_page.\n\n"
        f"Output JSON matching this schema:\n{schema_json}"
    )
```

- [ ] **Step 5: Update `_assign_files_in_batches` return signature + result collection**

Change the return type annotation to `tuple[dict[str, list[str]], dict[str, list[str]]]`, initialise a sibling `secondary: dict[str, list[str]] = {t: [] for t in valid_titles}`, and update the per-batch merge loop:

```python
        for a in raw.get("assignments", []):
            f = a.get("file", "")
            primary = a.get("primary_page", "")
            secondaries = a.get("secondary_pages", []) or []
            if f not in batch or f in assigned:
                continue
            if primary not in valid_titles_set:
                continue
            result[primary].append(f)
            assigned.add(f)
            for sec in secondaries[:2]:
                if sec in valid_titles_set and sec != primary:
                    secondary[sec].append(f)
```

Update the directory-clustering residue merge to only touch `result` (primary). Update every `return result` in this function to `return result, secondary`.

- [ ] **Step 6: Update `_assign_files` to consume the tuple**

Change `_assign_files`'s return type to `tuple[dict[str, list[str]], dict[str, list[str]]]`. Capture the tuple at every `await _assign_files_in_batches(...)` call:

```python
            result, secondary = await _assign_files_in_batches(...)
            _validate_assignments(result, outline)
            return result, secondary
```

Change the `_directory_cluster_assign` fallback at the end to:

```python
    return _directory_cluster_assign(outline, all_files), {t["title"]: [] for t in outline}
```

And update `generate_wiki_plan` (at the call site that reads `file_assignments = await _assign_files(...)`). Replace with:

```python
    primary_assignments, secondary_assignments = await _assign_files(...)
```

Then when building the final raw dict, include `secondary_files`:

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

- [ ] **Step 7: Update `validate_wiki_plan` to accept `secondary_files`**

In `validate_wiki_plan`, when constructing each `WikiPageSpec`, pass `secondary_files=p.get("secondary_files", [])`. Leave every other check (duplicate slugs, parent validity, orphan handling, depth, page-count) unchanged — secondary assignments do not count toward orphan detection, and the 25-file cap continues to apply to `files` only.

- [ ] **Step 8: Run the tests to verify they pass**

```bash
uv run pytest tests/worker/test_assign_files_batched.py tests/worker/test_wiki_planner.py -v
```

Expected: all pass. Pre-existing tests that call `_assign_files_in_batches` may need `result, _ = await _assign_files_in_batches(...)` — update them and re-run.

- [ ] **Step 9: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_assign_files_batched.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): return primary+secondary assignments from batched path"
```

---

### Task B3: Page generator includes secondary files as "referenced" context

**Files:**
- Modify: `worker/pipeline/page_generator.py` — extend context builder
- Test: `tests/worker/test_page_generator.py` (create if missing)

- [ ] **Step 1: Write the failing test**

Create or extend `tests/worker/test_page_generator.py`:

```python
"""Tests for secondary-file context injection (Layer C2)."""

from __future__ import annotations

from worker.pipeline.page_generator import _format_secondary_context
from worker.pipeline.wiki_planner import WikiPageSpec


def test_format_secondary_context_returns_empty_when_no_files():
    spec = WikiPageSpec(title="X", purpose="p", files=["a.py"], secondary_files=[])
    assert _format_secondary_context(spec, entity_summaries_by_file={}) == ""


def test_format_secondary_context_lists_referenced_files_with_summaries():
    spec = WikiPageSpec(
        title="Core",
        purpose="p",
        files=["a.py"],
        secondary_files=["shared/utils.py", "shared/io.py"],
    )
    summaries = {
        "shared/utils.py": "class Helper: ...",
        "shared/io.py": "def read(path): ...",
    }
    text = _format_secondary_context(spec, entity_summaries_by_file=summaries)
    assert "Referenced modules" in text
    assert "shared/utils.py" in text
    assert "class Helper" in text
    assert "shared/io.py" in text
    assert "def read" in text


def test_format_secondary_context_skips_files_without_summary():
    """A secondary file that is missing from the summaries dict must be
    silently skipped — do not emit a bare header with no content."""
    spec = WikiPageSpec(
        title="Core",
        purpose="p",
        files=["a.py"],
        secondary_files=["missing.py"],
    )
    text = _format_secondary_context(spec, entity_summaries_by_file={})
    assert text == ""
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/worker/test_page_generator.py -k "secondary" -v
```

Expected: FAIL — function does not exist.

- [ ] **Step 3: Add `_format_secondary_context` + wire into the draft builder**

In `worker/pipeline/page_generator.py`, add a helper near the existing context-building helpers:

```python
def _format_secondary_context(
    spec: WikiPageSpec,
    entity_summaries_by_file: dict[str, str],
) -> str:
    """Render the Pass-2 "referenced modules" section for secondary files.

    Secondary files are *not* source-of-truth for this page — they are
    owned by another page — but the LLM needs enough context to describe
    cross-page references correctly.  We emit a compact "Referenced
    modules" block listing each secondary file with its entity summary.

    Returns an empty string when there are no secondary files with
    summaries so the caller can unconditionally concatenate.
    """
    entries: list[str] = []
    for rel in spec.secondary_files or []:
        summary = entity_summaries_by_file.get(rel)
        if not summary:
            continue
        entries.append(f"- {rel}\n  {summary}")
    if not entries:
        return ""
    return "## Referenced modules (owned by other pages)\n" + "\n".join(entries)
```

Find the existing Pass-2 draft call site (search for `generate_draft(` in `page_generator.py`). Inject the secondary context into the prompt segments the draft receives. Typical shape:

```python
    secondary_block = _format_secondary_context(
        spec, entity_summaries_by_file=entity_summaries_by_file
    )
    if secondary_block:
        segments.append(PromptSegment(text=secondary_block, cacheable=False))
```

(If `entity_summaries_by_file` is not already assembled in this function, derive it from `file_analysis.files` immediately before calling the helper — same pattern as `spec.files` receives today.)

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/worker/test_page_generator.py -v
```

Expected: PASS.

- [ ] **Step 5: Confirm the source-files table stays primary-only**

The post-processing `_append_source_files_table(draft, spec.files or [])` at `page_generator.py:301` must still receive only `spec.files`, never `spec.secondary_files`. Verify the existing line is untouched, then add a regression test that exercises the full draft path with both primary and secondary files and asserts secondary paths appear in the body but not in the "Source files" table.

```python
def test_source_files_table_excludes_secondary_files():
    from worker.pipeline.page_generator import _append_source_files_table

    draft = "body text"
    out = _append_source_files_table(draft, ["a.py"])
    assert "a.py" in out
    # secondary files were never passed in, so they cannot appear
    assert "shared/utils.py" not in out
```

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/page_generator.py tests/worker/test_page_generator.py
git commit -m "feat(page_generator): inject secondary-file summaries as referenced context"
```

---

### Task B4: Refresh — primary vs secondary affected pages

**Files:**
- Modify: `worker/pipeline/ingestion.py` — `get_affected_pages` returns a structured result
- Modify: `worker/jobs.py` — refresh path consumes the new result
- Test: `tests/worker/test_ingestion.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/worker/test_ingestion.py`:

```python
def test_get_affected_pages_distinguishes_primary_from_secondary():
    from worker.pipeline.ingestion import AffectedPages, get_affected_pages
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Core",
                purpose="p",
                files=["core/a.py"],
                secondary_files=[],
            ),
            WikiPageSpec(
                title="API",
                purpose="p",
                files=["api/r.py"],
                secondary_files=["core/a.py"],  # references Core's file
            ),
        ]
    )
    result = get_affected_pages(["core/a.py"], plan)
    assert isinstance(result, AffectedPages)
    assert result.primary == {"Core"}
    assert result.secondary == {"API"}


def test_get_affected_pages_primary_wins_over_secondary():
    """If a changed file is primary for page P and secondary for page Q,
    P goes into primary and Q goes into secondary — never both."""
    from worker.pipeline.ingestion import get_affected_pages
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="P", purpose="p", files=["x.py"], secondary_files=[]
            ),
            WikiPageSpec(
                title="Q", purpose="p", files=[], secondary_files=["x.py"]
            ),
        ]
    )
    result = get_affected_pages(["x.py"], plan)
    assert result.primary == {"P"}
    assert result.secondary == {"Q"}


def test_get_affected_pages_legacy_plans_without_secondary_still_work():
    """WikiPlan instances saved before Stage B must keep working.  When
    ``secondary_files`` is empty everywhere, ``secondary`` in the result
    must be an empty set."""
    from worker.pipeline.ingestion import get_affected_pages
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(pages=[WikiPageSpec(title="A", purpose="p", files=["f.py"])])
    result = get_affected_pages(["f.py"], plan)
    assert result.primary == {"A"}
    assert result.secondary == set()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/worker/test_ingestion.py -k "affected_pages" -v
```

Expected: FAIL — `AffectedPages` does not exist.

- [ ] **Step 3: Rework `get_affected_pages`**

In `worker/pipeline/ingestion.py`, replace the function (current lines 387-424) with:

```python
from dataclasses import dataclass


@dataclass
class AffectedPages:
    """Result of :func:`get_affected_pages`.

    ``primary`` — page titles whose *primary* file set overlaps with the
    changed file set.  These pages must be regenerated in the current
    refresh cycle.

    ``secondary`` — page titles whose *secondary* file set overlaps but
    whose primary set does not.  These pages are marked stale and
    regenerated in the next refresh cycle (deferred, to keep refresh
    cost bounded).  A page never appears in both sets — primary wins.
    """

    primary: set[str]
    secondary: set[str]


def get_affected_pages(
    changed_files: list[str], wiki_plan: WikiPlan
) -> AffectedPages:
    """Return pages affected by the changed files, split by assignment kind.

    Supersedes the single-set variant: callers that only care about
    regenerate-now semantics should read :attr:`AffectedPages.primary`.
    """
    changed = set(changed_files)
    primary: set[str] = set()
    secondary: set[str] = set()
    for page in wiki_plan.pages:
        if any(f in changed for f in (page.files or [])):
            primary.add(page.title)
            continue  # primary wins
        if any(f in changed for f in (page.secondary_files or [])):
            secondary.add(page.title)
    return AffectedPages(primary=primary, secondary=secondary)
```

- [ ] **Step 4: Update callers in `worker/jobs.py`**

Search `worker/jobs.py` for every read of `get_affected_pages(` and unpack `.primary`. The refresh stage should regenerate only the primary set in this cycle but record the secondary set for the next cycle — add a log line and a small TODO-free implementation:

```python
    affected = get_affected_pages(changed_files, old_plan)
    logger.info(
        "Refresh affects %d primary pages and %d secondary pages (deferred)",
        len(affected.primary),
        len(affected.secondary),
    )
    pages_to_regenerate = affected.primary
```

Secondary-only staleness is persisted as an artefact for the next refresh by appending a line to `ast/stale_secondary.json` (one JSON array, overwrite on each refresh):

```python
    stale_path = ast_dir / "stale_secondary.json"
    await _write_text_async(stale_path, json.dumps(sorted(affected.secondary)))
```

At the start of the *next* refresh, read `stale_secondary.json` if it exists and union those titles into `pages_to_regenerate`. Keep the file deletion after consumption so a third refresh does not double-regenerate:

```python
    prior_stale: set[str] = set()
    if stale_path.exists():
        try:
            prior_stale = set(json.loads(stale_path.read_text()))
        except (OSError, json.JSONDecodeError):
            prior_stale = set()
        stale_path.unlink()
    pages_to_regenerate = affected.primary | prior_stale
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/worker/test_ingestion.py tests/worker/test_jobs.py -v
```

Expected: all pass. Existing jobs tests may need to be taught about the new dataclass — e.g. a test that asserted `get_affected_pages(...) == {"A"}` now asserts `.primary == {"A"}`.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/ingestion.py worker/jobs.py tests/worker/test_ingestion.py tests/worker/test_jobs.py
git commit -m "feat(refresh): split affected pages into primary (eager) and secondary (deferred)"
```

---

### Task B5: `validate_wiki_plan` handles orphans against primary + secondary

**Files:**
- Modify: `worker/pipeline/wiki_planner.py` — `validate_wiki_plan`
- Test: `tests/worker/test_wiki_planner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/worker/test_wiki_planner.py`:

```python
def test_validate_wiki_plan_considers_secondary_assignment_for_orphans():
    """A file that is secondary on some page still counts as assigned."""
    from worker.pipeline.wiki_planner import validate_wiki_plan

    raw = {
        "pages": [
            {
                "title": "Overview",
                "purpose": "top",
                "files": ["x.py"],
                "secondary_files": [],
            },
            {
                "title": "Core",
                "purpose": "core",
                "files": ["core.py"],
                "secondary_files": ["shared.py"],
            },
        ]
    }
    plan = validate_wiki_plan(
        raw, all_files=["x.py", "core.py", "shared.py"]
    )
    # ``shared.py`` must NOT be appended to Overview as an orphan because
    # it's already referenced as secondary on Core.
    overview = next(p for p in plan.pages if p.title == "Overview")
    assert "shared.py" not in overview.files
    core = next(p for p in plan.pages if p.title == "Core")
    assert core.secondary_files == ["shared.py"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_validate_wiki_plan_considers_secondary_assignment_for_orphans -v
```

Expected: FAIL — orphan detection still counts only `files`.

- [ ] **Step 3: Update the orphan-detection block**

In `validate_wiki_plan`, change the `assigned` set construction (around line 1066):

```python
    if all_files:
        assigned = {f for page in pages for f in page.files}
        assigned |= {f for page in pages for f in page.secondary_files}
        orphans = [f for f in all_files if f not in assigned]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/worker/test_wiki_planner.py::test_validate_wiki_plan_considers_secondary_assignment_for_orphans -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/pipeline/wiki_planner.py tests/worker/test_wiki_planner.py
git commit -m "feat(planner): treat secondary assignments as assigned in orphan check"
```

---

### Task B6: Lint + full-suite verification for Stage B

- [ ] **Step 1: Pre-commit checks**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: clean.

- [ ] **Step 2: Full backend suite**

```bash
uv run pytest tests/ --ignore=tests/e2e -q
```

Expected: all pass.

- [ ] **Step 3: Frontend lint (unchanged surface but CLAUDE.md requires it)**

```bash
cd web && npm run lint
```

Expected: clean.

- [ ] **Step 4: Fix any regressions before moving to Stage C**

---

## Stage C — Stage Validation Harness

**Objective:** Let maintainers introspect planner output — or replay a run against saved fixtures — without spending live LLM budget. Two parts:

1. **Fixture recorder** — a small module that dumps `outline.json`, `assignments.json`, and the final `wiki_plan.json` side-by-side whenever `AUTOWIKI_RECORD_PLANNER_FIXTURES=1` is set for a run. Fixtures live at `~/.autowiki/repos/{repo_hash}/fixtures/`.
2. **`autowiki validate-plan <repo>` CLI** — reads the stored `wiki_plan.json` (plus fixtures if they exist) and reports:
   - Total files, total pages, orphan count, coverage %.
   - Primary/secondary per-page size distribution (min / p50 / p90 / max, plus a histogram).
   - Any validation failures produced by re-running `validate_wiki_plan` on the saved plan.
   - Directory locality score (how many primary-assigned files share a top-level directory with the majority of other primary-assigned files on the same page).

The command is read-only — it neither regenerates nor modifies state.

### Task C1: Fixture recorder

**Files:**
- Create: `worker/pipeline/fixture_recorder.py`
- Create: `tests/worker/test_fixture_recorder.py`

- [ ] **Step 1: Write the failing test**

Create `tests/worker/test_fixture_recorder.py`:

```python
"""Tests for the fixture recorder."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from worker.pipeline.fixture_recorder import (
    FixtureRecorder,
    is_recording_enabled,
)


def test_is_recording_enabled_respects_env(monkeypatch):
    monkeypatch.delenv("AUTOWIKI_RECORD_PLANNER_FIXTURES", raising=False)
    assert is_recording_enabled() is False
    monkeypatch.setenv("AUTOWIKI_RECORD_PLANNER_FIXTURES", "1")
    assert is_recording_enabled() is True
    monkeypatch.setenv("AUTOWIKI_RECORD_PLANNER_FIXTURES", "0")
    assert is_recording_enabled() is False


def test_recorder_writes_each_stage_to_a_separate_file(tmp_path):
    rec = FixtureRecorder(root=tmp_path)
    rec.record_outline([{"title": "A", "purpose": "p"}])
    rec.record_assignments(
        primary={"A": ["x.py"]}, secondary={"A": []}
    )
    rec.record_wiki_plan({"pages": [{"title": "A", "files": ["x.py"]}]})
    assert (tmp_path / "outline.json").exists()
    assert (tmp_path / "assignments.json").exists()
    assert (tmp_path / "wiki_plan.json").exists()
    assert json.loads((tmp_path / "outline.json").read_text()) == [
        {"title": "A", "purpose": "p"}
    ]
    payload = json.loads((tmp_path / "assignments.json").read_text())
    assert payload["primary"] == {"A": ["x.py"]}
    assert payload["secondary"] == {"A": []}


def test_recorder_is_noop_when_root_is_none(tmp_path):
    rec = FixtureRecorder(root=None)
    rec.record_outline([{"title": "A", "purpose": "p"}])
    # No files written, no exception raised
    assert list(tmp_path.iterdir()) == []


def test_recorder_overwrites_existing_files(tmp_path):
    (tmp_path / "outline.json").write_text("stale")
    rec = FixtureRecorder(root=tmp_path)
    rec.record_outline([{"title": "new"}])
    assert json.loads((tmp_path / "outline.json").read_text()) == [
        {"title": "new"}
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/worker/test_fixture_recorder.py -v
```

Expected: ImportError — module does not exist.

- [ ] **Step 3: Implement the recorder**

Create `worker/pipeline/fixture_recorder.py`:

```python
"""Record planner intermediate artefacts for offline replay.

Gated on the ``AUTOWIKI_RECORD_PLANNER_FIXTURES`` env var so production
runs do not pay the I/O cost.  Recorded fixtures are consumed by
``autowiki validate-plan`` to produce a diagnostic report without
spending live LLM budget.

Fixture layout (relative to the per-repo data dir)::

    fixtures/
      outline.json          # Phase-1 outline (list of page dicts)
      assignments.json      # {"primary": {...}, "secondary": {...}}
      wiki_plan.json        # final validated plan (same shape as
                            # ast/wiki_plan.json; duplicated so the
                            # fixtures dir is self-contained)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def is_recording_enabled() -> bool:
    """Return True when ``AUTOWIKI_RECORD_PLANNER_FIXTURES=1`` in the env."""
    return os.environ.get("AUTOWIKI_RECORD_PLANNER_FIXTURES", "0") == "1"


class FixtureRecorder:
    """Writes JSON fixtures under ``root``.  No-op when ``root is None``."""

    def __init__(self, root: Path | None) -> None:
        self.root = root
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)

    def _write(self, filename: str, payload: Any) -> None:
        if self.root is None:
            return
        (self.root / filename).write_text(
            json.dumps(payload, indent=2, default=_default_encoder)
        )

    def record_outline(self, outline: list[dict]) -> None:
        self._write("outline.json", outline)

    def record_assignments(
        self,
        primary: dict[str, list[str]],
        secondary: dict[str, list[str]],
    ) -> None:
        self._write(
            "assignments.json", {"primary": primary, "secondary": secondary}
        )

    def record_wiki_plan(self, plan: dict) -> None:
        self._write("wiki_plan.json", plan)


def _default_encoder(obj: Any) -> Any:
    # Support dataclasses / sets so callers do not need to flatten manually.
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj)!r} is not JSON serializable")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/worker/test_fixture_recorder.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Wire the recorder into `generate_wiki_plan`**

In `worker/pipeline/wiki_planner.py`, import the module:

```python
from worker.pipeline.fixture_recorder import FixtureRecorder, is_recording_enabled
```

Add an optional `fixture_recorder: FixtureRecorder | None = None` parameter to `generate_wiki_plan`. After Phase 1 completes, call `fixture_recorder.record_outline(outline)` when the recorder is not `None`. After Phase 2: `fixture_recorder.record_assignments(primary_assignments, secondary_assignments)`. After `validate_wiki_plan` returns: `fixture_recorder.record_wiki_plan(plan.to_internal_json())`.

In `worker/jobs.py`, construct the recorder once per run:

```python
    fixture_recorder = (
        FixtureRecorder(root=repo_data_dir / "fixtures")
        if is_recording_enabled()
        else None
    )
```

and forward it into the `generate_wiki_plan(...)` call.

- [ ] **Step 6: Commit**

```bash
git add worker/pipeline/fixture_recorder.py worker/pipeline/wiki_planner.py worker/jobs.py tests/worker/test_fixture_recorder.py
git commit -m "feat(planner): record outline + assignments + plan fixtures under env flag"
```

---

### Task C2: `autowiki validate-plan` CLI

**Files:**
- Create: `cli/commands/validate_plan.py`
- Modify: `cli/main.py` — register the command
- Create: `tests/cli/test_validate_plan.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_validate_plan.py`:

```python
"""Integration tests for ``autowiki validate-plan``."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def _write_plan(dirpath: Path, plan: dict) -> None:
    (dirpath / "ast").mkdir(parents=True, exist_ok=True)
    (dirpath / "ast" / "wiki_plan.json").write_text(json.dumps(plan))


def test_validate_plan_reports_coverage_and_page_sizes(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repos" / "owner-repo"
    _write_plan(
        repo_dir,
        {
            "repo_notes": [{"content": ""}],
            "pages": [
                {
                    "title": "Overview",
                    "purpose": "top",
                    "files": ["a.py", "b.py"],
                    "secondary_files": [],
                },
                {
                    "title": "Core",
                    "purpose": "core",
                    "files": [f"core/{i}.py" for i in range(5)],
                    "secondary_files": ["a.py"],
                },
            ],
        },
    )
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))

    result = runner.invoke(app, ["validate-plan", "owner-repo"])
    assert result.exit_code == 0, result.output
    assert "Pages: 2" in result.output
    assert "Primary files: 7" in result.output
    assert "Secondary assignments: 1" in result.output
    # Page sizes
    assert "Overview" in result.output
    assert "Core" in result.output
    # A distribution line exists
    assert "p50" in result.output or "median" in result.output.lower()


def test_validate_plan_reports_validation_failure(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repos" / "bad-repo"
    _write_plan(
        repo_dir,
        {
            "repo_notes": [],
            "pages": [
                {
                    "title": "Overview",
                    "purpose": "top",
                    "files": [f"f{i}.py" for i in range(30)],  # > 25 cap
                    "secondary_files": [],
                }
            ],
        },
    )
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))

    result = runner.invoke(app, ["validate-plan", "bad-repo"])
    assert result.exit_code == 1
    assert "VALIDATION FAILURE" in result.output
    assert "30 files" in result.output


def test_validate_plan_missing_repo_returns_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["validate-plan", "does-not-exist"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/cli/test_validate_plan.py -v
```

Expected: command not registered → failure.

- [ ] **Step 3: Implement the command**

Create `cli/commands/validate_plan.py`:

```python
"""``autowiki validate-plan`` — offline planner introspection.

Reads ``{data_dir}/repos/{repo}/ast/wiki_plan.json`` and reports
coverage, per-page size distribution, and any validation failures
without running the pipeline.  No LLM calls, no git clone, no writes.
"""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

import typer

from worker.pipeline.wiki_planner import (
    WikiPageSpec,
    WikiPlan,
    validate_wiki_plan,
)


def _data_dir() -> Path:
    raw = os.environ.get("AUTOWIKI_DATA_DIR")
    if raw:
        return Path(raw)
    return Path.home() / ".autowiki"


def _load_plan(plan_path: Path) -> WikiPlan:
    data = json.loads(plan_path.read_text())
    pages = [
        WikiPageSpec(
            title=p["title"],
            purpose=p.get("purpose", ""),
            parent=p.get("parent"),
            files=p.get("files", []),
            secondary_files=p.get("secondary_files", []),
        )
        for p in data.get("pages", [])
    ]
    return WikiPlan(repo_notes=data.get("repo_notes", []), pages=pages)


def _percentile(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(p * (len(ordered) - 1)))
    return float(ordered[idx])


def validate_plan_cmd(
    repo: str = typer.Argument(
        ..., help="Repo identifier as stored under ~/.autowiki/repos/"
    ),
) -> None:
    """Report coverage, sizes, and validation status of a stored plan."""
    data_dir = _data_dir()
    plan_path = data_dir / "repos" / repo / "ast" / "wiki_plan.json"
    if not plan_path.is_file():
        typer.echo(f"Error: wiki plan not found at {plan_path}", err=True)
        raise typer.Exit(1)

    plan = _load_plan(plan_path)
    total_primary = sum(len(p.files) for p in plan.pages)
    total_secondary = sum(len(p.secondary_files) for p in plan.pages)
    sizes = [len(p.files) for p in plan.pages]

    typer.echo(f"Pages: {len(plan.pages)}")
    typer.echo(f"Primary files: {total_primary}")
    typer.echo(f"Secondary assignments: {total_secondary}")
    typer.echo("")
    typer.echo("Per-page primary file distribution:")
    typer.echo(
        f"  min={min(sizes, default=0)} "
        f"p50={_percentile(sizes, 0.5):.0f} "
        f"p90={_percentile(sizes, 0.9):.0f} "
        f"max={max(sizes, default=0)} "
        f"mean={statistics.mean(sizes) if sizes else 0:.1f}"
    )
    typer.echo("")
    typer.echo("Per-page breakdown:")
    for p in plan.pages:
        typer.echo(
            f"  - {p.title}: primary={len(p.files)} "
            f"secondary={len(p.secondary_files)}"
        )
    typer.echo("")

    try:
        validate_wiki_plan(
            {
                "pages": [
                    {
                        "title": p.title,
                        "purpose": p.purpose,
                        "parent": p.parent,
                        "files": p.files,
                        "secondary_files": p.secondary_files,
                    }
                    for p in plan.pages
                ]
            },
        )
        typer.echo("Validation: OK")
    except ValueError as exc:
        typer.echo(f"VALIDATION FAILURE: {exc}", err=False)
        raise typer.Exit(1)
```

Modify `cli/main.py` to register the command:

```python
from cli.commands.validate_plan import validate_plan_cmd
...
app.command("validate-plan")(validate_plan_cmd)
```

- [ ] **Step 4: Make sure `tests/cli/` is a package**

```bash
ls tests/cli/__init__.py || echo "missing"
```

If the directory does not exist, create it and add an empty `__init__.py`:

```bash
mkdir -p tests/cli && touch tests/cli/__init__.py
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/cli/test_validate_plan.py -v
```

Expected: 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add cli/main.py cli/commands/validate_plan.py tests/cli/__init__.py tests/cli/test_validate_plan.py
git commit -m "feat(cli): add 'autowiki validate-plan' for offline planner inspection"
```

---

### Task C3: Locality score diagnostic

**Files:**
- Modify: `cli/commands/validate_plan.py` — add locality score
- Test: `tests/cli/test_validate_plan.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_validate_plan.py`:

```python
def test_validate_plan_reports_locality_score(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repos" / "locality-repo"
    _write_plan(
        repo_dir,
        {
            "repo_notes": [],
            "pages": [
                {
                    "title": "Worker Pipeline",
                    "purpose": "p",
                    "files": [
                        "worker/pipeline/a.py",
                        "worker/pipeline/b.py",
                        "api/routes.py",  # cross-directory — hurts locality
                    ],
                    "secondary_files": [],
                },
                {
                    "title": "Core",
                    "purpose": "p",
                    "files": ["core/a.py", "core/b.py"],
                    "secondary_files": [],
                },
            ],
        },
    )
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["validate-plan", "locality-repo"])
    assert result.exit_code == 0, result.output
    assert "Locality score" in result.output
    # Worker Pipeline page should register ≤ 1.0 but > 0 locality
    # Core page (100% same-dir) should be 1.0
    assert "Core: 1.00" in result.output
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/cli/test_validate_plan.py::test_validate_plan_reports_locality_score -v
```

Expected: FAIL.

- [ ] **Step 3: Add the locality helper and output block**

Append to `cli/commands/validate_plan.py` (inside the existing function, after the per-page breakdown):

```python
    def _locality_score(page: WikiPageSpec) -> float:
        if not page.files:
            return 1.0
        counts: dict[str, int] = {}
        for f in page.files:
            key = f.split("/", 1)[0] if "/" in f else "(root)"
            counts[key] = counts.get(key, 0) + 1
        top = max(counts.values())
        return top / len(page.files)

    typer.echo("Locality score (fraction of primary files in top directory):")
    for p in plan.pages:
        score = _locality_score(p)
        typer.echo(f"  - {p.title}: {score:.2f}")
    typer.echo("")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/cli/test_validate_plan.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add cli/commands/validate_plan.py tests/cli/test_validate_plan.py
git commit -m "feat(cli): report per-page locality score in validate-plan"
```

---

### Task C4: Document the harness in CLAUDE.md and README

**Files:**
- Modify: `CLAUDE.md` — add environment variable and CLI entry
- Modify: `README.md` (only if it currently lists CLI commands — check first)

- [ ] **Step 1: Check whether README lists CLI commands**

```bash
grep -n "autowiki index" README.md
```

If the grep returns matches, the README needs an update. Otherwise skip to Step 2.

- [ ] **Step 2: Update CLAUDE.md**

Under the CLI section (current `### CLI (Phase 1 + Phase 3)`), add:

```markdown
autowiki validate-plan <repo>       # Offline planner diagnostic — reads ast/wiki_plan.json and reports coverage, page-size distribution, and validation status
```

Under "Key Implementation Notes", append:

```markdown
- **Planner fixture recorder**: set `AUTOWIKI_RECORD_PLANNER_FIXTURES=1` to dump `outline.json`, `assignments.json`, and `wiki_plan.json` under `~/.autowiki/repos/{repo}/fixtures/` during a live run.  `autowiki validate-plan` reads only `ast/wiki_plan.json` today — future work can extend it to replay stages from the fixtures without live API calls.
- **Multi-page assignments (Layer C2)**: `_ASSIGNMENT_SCHEMA` emits `{file, primary_page, secondary_pages: [...≤2]}`.  `WikiPageSpec.secondary_files` stores per-page referenced-but-not-owned files; the page generator injects them into the prompt as a "Referenced modules" block but excludes them from the source-files table.  `get_affected_pages` returns `AffectedPages(primary=..., secondary=...)` — primary pages regenerate in the current refresh, secondary pages are persisted to `ast/stale_secondary.json` and regenerated in the next refresh cycle.
- **Outline anchors (Layer C1)**: `generate_wiki_plan` accepts `clone_root` and synthesises three architectural signals from existing artefacts via `worker/pipeline/outline_anchors.py`: a directory tree (≤3 levels, with file counts), up to 25 package-entry docstrings (`__init__.py`, `mod.rs`, `index.ts`), and the `##`/`###` headings of the README.  These are injected into the Phase-1 outline prompt under an "Architectural anchors" section and significantly reduce cross-page fragmentation of cohesive subsystems.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document planner fixture recorder, Layer C1 anchors, and Layer C2 assignment"
```

---

### Task C5: Final full-suite verification

- [ ] **Step 1: Pre-commit checks**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: clean.

- [ ] **Step 2: Full backend suite**

```bash
uv run pytest tests/ --ignore=tests/e2e -q
```

Expected: all pass.

- [ ] **Step 3: Frontend lint**

```bash
cd web && npm run lint
```

Expected: clean.

- [ ] **Step 4: Manual smoke of the CLI**

Against an existing indexed repo:

```bash
uv run autowiki validate-plan <some-repo-from-your-local-dir>
```

Expected output: page count, file totals, size distribution, per-page breakdown, locality scores, and a trailing `Validation: OK` (or a validation failure). Exit code `0` on success.

- [ ] **Step 5: Optional — confirm the recorder produces fixtures**

```bash
AUTOWIKI_RECORD_PLANNER_FIXTURES=1 uv run autowiki index github.com/pallets/click
ls ~/.autowiki/repos/*/fixtures/
```

Expected: `outline.json`, `assignments.json`, `wiki_plan.json` exist once indexing completes.

- [ ] **Step 6: Open the PR**

```bash
git push -u origin feature/deferred-planner-improvements
gh pr create --title "feat(planner): outline anchors + multi-page assignment + validate-plan CLI" --body "$(cat <<'EOF'
## Summary
- Stage A (Layer C1): new `worker/pipeline/outline_anchors.py` derives a directory tree (≤3 levels, file counts), package-entry docstrings (Python `__init__.py`, Rust `mod.rs`, JS/TS `index.ts`), and README `##`/`###` headings, and surfaces them under an "Architectural anchors" section in the Phase-1 outline prompt.
- Stage B (Layer C2): `_ASSIGNMENT_SCHEMA` carries `{file, primary_page, secondary_pages: [...≤2]}`; `WikiPageSpec` gains `secondary_files`; the page generator injects secondary file summaries as "Referenced modules" context; `get_affected_pages` returns `AffectedPages(primary, secondary)` so refresh regenerates primary pages eagerly and defers secondary-only staleness to the next cycle via `ast/stale_secondary.json`.
- Stage C (validation harness): `worker/pipeline/fixture_recorder.py` + `AUTOWIKI_RECORD_PLANNER_FIXTURES=1` env flag dump planner intermediates under `~/.autowiki/repos/{repo}/fixtures/`; new `autowiki validate-plan <repo>` CLI reports coverage, size distribution, per-page locality score, and re-runs `validate_wiki_plan` offline.

## Motivation
Parent plan `docs/superpowers/plans/2026-04-15-wiki-planner-robustness.md` explicitly deferred these three items.  With observability and batched assignment in place, the next leverage points are (1) stop fragmenting cohesive subsystems during Phase 1, (2) represent genuine cross-page file relationships, and (3) make planner output inspectable without burning API budget.

## Test plan
- [ ] `uv run pytest tests/ --ignore=tests/e2e -q`
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `cd web && npm run lint`
- [ ] Manual: `autowiki validate-plan <known-repo>` prints a full report and exits 0
- [ ] Manual: `AUTOWIKI_RECORD_PLANNER_FIXTURES=1 autowiki index github.com/pallets/click` produces `fixtures/outline.json`, `fixtures/assignments.json`, `fixtures/wiki_plan.json`

## Out of scope / next
- Replaying planner stages from fixtures end-to-end (today `validate-plan` only consumes `wiki_plan.json`; replay through `_assign_files_in_batches` is a natural follow-up).
- A UI surface for `secondary_files` — currently only `secondary_file_count` leaks through `to_api_structure`.
EOF
)"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** Each of the three deferred items from the parent plan has a dedicated stage.
  - Layer C1 outline anchors → Stage A (Tasks A1-A4).
  - Layer C2 multi-page file assignment → Stage B (Tasks B1-B6).
  - Independent stage validation harness → Stage C (Tasks C1-C5).
- [x] **Placeholder scan:** No "TBD" / "TODO" / "add validation" / "fill in later". Every step contains concrete code and concrete commands.
- [x] **Type consistency:** `PackageDoc(package, docstring)` identical across helper + formatter tests. `AffectedPages(primary: set[str], secondary: set[str])` identical across definition + callers. `_assign_files_in_batches(...) -> tuple[dict[str, list[str]], dict[str, list[str]]]` matches `_assign_files`'s new return type and the tests that unpack it. `WikiPageSpec.secondary_files: list[str]` consistent across `to_internal_json`, `validate_wiki_plan`, and the page generator. `FixtureRecorder.record_*` method signatures match the tests.
- [x] **Signature stability:** `generate_wiki_plan(..., clone_root: Path | None = None, fixture_recorder: FixtureRecorder | None = None)` is the final signature; Stage A introduces `clone_root`, Stage C introduces `fixture_recorder` — later tasks do not rename them. `_build_outline_prompt(..., anchors_block: str | None = None)` is introduced in Task A2 and not renamed in A3. `_ASSIGNMENT_SCHEMA` change (`primary_page` + `secondary_pages`) is the canonical shape consumed by `_build_batch_assignment_user`, `_assign_files_in_batches`, and `validate_wiki_plan`.
- [x] **Fallback invariants preserved:** Directory-clustering fallback and the observability logging conventions from the parent plan are *not* touched by any stage here. Stage B explicitly only changes the tuple return shape; the residue path still routes through `_directory_cluster_assign`.
