"""Extract import/dependency relationships between source files and cluster modules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DependencyGraph:
    """Directed graph of file-level import relationships for a repository.

    Built by :func:`build_dependency_graph` and consumed by the wiki planner
    (for context) and the page generator (via :func:`summarize_page_deps`).

    Attributes:
        edges: Adjacency list mapping each source file's repository-relative
            path to the sorted list of other repository-relative paths it
            imports.  Only *internal* dependencies (files that exist inside
            the repo) appear here.  Example::

                {
                    "src/app.py": ["src/db.py", "src/utils.py"],
                    "src/db.py":  ["src/utils.py"],
                }

        clusters: Groups of files that are mutually reachable via import edges,
            computed with union-find.  Each inner list is sorted alphabetically;
            the outer list is sorted by descending cluster size (largest
            cluster first).  Files with no import relationships form
            singleton clusters.  Example::

                [
                    ["src/app.py", "src/db.py", "src/utils.py"],
                    ["tests/test_utils.py"],
                ]

        external_deps: Mapping from a source file's repository-relative path
            to the sorted list of *external* package/module names that it
            imports (i.e. strings that could not be resolved to a file inside
            the repo).  Example::

                {
                    "src/app.py": ["fastapi", "pydantic"],
                    "src/db.py":  ["sqlalchemy"],
                }
    """

    edges: dict[str, list[str]] = field(
        default_factory=dict
    )  # file -> [imported files]
    clusters: list[list[str]] = field(default_factory=list)  # groups of connected files
    external_deps: dict[str, list[str]] = field(
        default_factory=dict
    )  # file -> [external packages]


# ── Language-specific import patterns ──────────────────────────────────────────

_PYTHON_IMPORT = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+(?:\s*,\s*[\w.]+)*))",
    re.MULTILINE,
)
_JS_TS_IMPORT = re.compile(
    r"""(?:import\s+.*?\s+from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\))""",
    re.MULTILINE,
)
_GO_IMPORT = re.compile(r'^\s*(?:import\s+"([^"]+)"|"([^"]+)")', re.MULTILINE)
_JAVA_KOTLIN_IMPORT = re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)
_RUST_USE = re.compile(r"^\s*(?:use\s+([\w:]+)|mod\s+(\w+))", re.MULTILINE)
_C_INCLUDE = re.compile(r'^\s*#include\s+"([^"]+)"', re.MULTILINE)

_LANG_PATTERNS: dict[str, re.Pattern] = {
    ".py": _PYTHON_IMPORT,
    ".js": _JS_TS_IMPORT,
    ".jsx": _JS_TS_IMPORT,
    ".ts": _JS_TS_IMPORT,
    ".tsx": _JS_TS_IMPORT,
    ".go": _GO_IMPORT,
    ".java": _JAVA_KOTLIN_IMPORT,
    ".kt": _JAVA_KOTLIN_IMPORT,
    ".kts": _JAVA_KOTLIN_IMPORT,
    ".rs": _RUST_USE,
    ".c": _C_INCLUDE,
    ".h": _C_INCLUDE,
    ".cpp": _C_INCLUDE,
    ".cc": _C_INCLUDE,
    ".cs": _JAVA_KOTLIN_IMPORT,
}


def _extract_imports(path: Path, source: str) -> list[str]:
    """Extract raw import/module strings from a source file using regex.

    Selects the appropriate language pattern from :data:`_LANG_PATTERNS`
    based on the file extension, then returns all matched import strings.
    Handles comma-separated Python imports (``import os, sys``) by splitting
    on commas and yielding each part individually.

    Args:
        path: :class:`~pathlib.Path` to the source file.  Only the suffix is
            used to look up the regex pattern; the file is **not** re-read
            here (the caller passes *source* directly).
        source: Full text content of the file as a ``str``.

    Returns:
        A ``list[str]`` of raw import strings exactly as they appear in the
        source (e.g. module paths, package names, or relative specifiers).
        Returns an empty list for unsupported file extensions.

    Example::

        # Python file containing:
        #   from worker.llm.base import LLMProvider
        #   import os, sys
        imports = _extract_imports(Path("worker/pipeline/foo.py"), source)
        # imports == ["worker.llm.base", "os", "sys"]

        # TypeScript file containing:
        #   import { useState } from "react"
        #   const x = require("./utils")
        imports = _extract_imports(Path("web/app.ts"), source)
        # imports == ["react", "./utils"]
    """
    pattern = _LANG_PATTERNS.get(path.suffix.lower())
    if pattern is None:
        return []
    raw: list[str] = []
    for match in pattern.finditer(source):
        # Each pattern may have multiple groups; take first non-None
        for group in match.groups():
            if group is not None:
                # Handle comma-separated imports (e.g., "import os, sys")
                for part in group.split(","):
                    part = part.strip()
                    if part:
                        raw.append(part)
                break
    return raw


def _resolve_import(
    raw_import: str,
    source_file: str,
    file_index: dict[str, str],
    suffix: str,
) -> str | None:
    """Try to resolve a raw import string to a known file path in the repo.

    Normalises the import string by converting Python dot-separators and Rust
    double-colon separators to forward slashes, then probes a set of candidate
    paths against *file_index*.  When a direct match fails, the function
    progressively strips leading path components to handle relative imports
    (e.g. ``from .utils import helper`` → ``utils``).

    Fallback logic (in order):
    1. Exact match on the normalised path (e.g. ``"worker/llm/base"``).
    2. Normalised path with the source file's own extension appended
       (e.g. ``"worker/llm/base.py"``).
    3. ``"{normalised}/index{suffix}"`` — JS/TS index-file convention.
    4. ``"{normalised}/__init__.py"`` — Python package init.
    5. ``"{normalised}/mod.rs"`` — Rust module file.
    6. For each candidate, repeat steps 1–5 with successive leading components
       stripped (handles relative/partial imports).

    Returns ``None`` when none of the candidates match any key in *file_index*
    — this indicates an *external* dependency (a third-party package).

    Args:
        raw_import: The import string as returned by :func:`_extract_imports`,
            e.g. ``"worker.llm.base"``, ``"./utils"``, or ``"react"``.
        source_file: Repository-relative path of the file that contains the
            import.  Passed for potential future use (relative-import
            resolution); currently not used in the candidate generation.
        file_index: Mapping from repository-relative path strings (with and
            without extension) to canonical relative path strings, built by
            :func:`build_dependency_graph`.
        suffix: The file extension of *source_file* (e.g. ``".py"``), used
            to construct the ``"{normalised}{suffix}"`` candidate.

    Returns:
        The canonical repository-relative path ``str`` if a match is found
        (e.g. ``"worker/llm/base.py"``), or ``None`` if the import cannot be
        resolved to a file inside the repo.

    Example::

        index = {"worker/llm/base": "worker/llm/base.py",
                 "worker/llm/base.py": "worker/llm/base.py"}
        result = _resolve_import(
            "worker.llm.base", "worker/pipeline/foo.py", index, ".py"
        )
        # result == "worker/llm/base.py"

        result = _resolve_import("fastapi", "worker/pipeline/foo.py", index, ".py")
        # result == None  (external dependency)
    """
    # Normalize: dots to slashes (Python), colons to slashes (Rust)
    normalized = raw_import.replace(".", "/").replace("::", "/")

    # Try direct match and common suffixes
    candidates = [
        normalized,
        f"{normalized}{suffix}",
        f"{normalized}/index{suffix}",
        f"{normalized}/__init__.py",
        f"{normalized}/mod.rs",
    ]

    for candidate in candidates:
        if candidate in file_index:
            return file_index[candidate]
        # Try without leading components (relative imports)
        parts = candidate.split("/")
        for i in range(1, len(parts)):
            sub = "/".join(parts[i:])
            if sub in file_index:
                return file_index[sub]

    return None


def build_dependency_graph(
    files: list[Path],
    root: Path,
) -> DependencyGraph:
    """Build a :class:`DependencyGraph` from the source files under *root*.

    For each file, reads its text content, extracts import strings with
    :func:`_extract_imports`, and attempts to resolve each import to another
    file in the repo via :func:`_resolve_import`.  Unresolvable imports are
    classified as external dependencies.  After all edges are collected,
    :func:`_compute_clusters` groups files into connected components.

    Args:
        files: List of :class:`~pathlib.Path` objects to analyse.  Files that
            cannot be made relative to *root* or cannot be read are silently
            skipped.
        root: The repository root :class:`~pathlib.Path`.  Used to compute
            repository-relative keys for the file index and graph edges.

    Returns:
        A populated :class:`DependencyGraph` with ``edges``, ``clusters``,
        and ``external_deps`` filled in.

    Example::

        from pathlib import Path
        root = Path("/repos/myproject")
        graph = build_dependency_graph(list(root.rglob("*.py")), root)
        # graph.edges == {
        #     "src/app.py": ["src/db.py", "src/utils.py"],
        #     "src/db.py":  ["src/utils.py"],
        # }
        # graph.clusters == [
        #     ["src/app.py", "src/db.py", "src/utils.py"],
        #     ["tests/test_app.py"],
        # ]
        # graph.external_deps == {"src/app.py": ["fastapi"]}
    """
    graph = DependencyGraph()

    # Build index of relative paths for resolution
    rel_paths: dict[str, str] = {}  # relative_path -> relative_path
    file_to_rel: dict[Path, str] = {}
    for f in files:
        try:
            rel = str(f.relative_to(root))
        except ValueError:
            continue
        rel_paths[rel] = rel
        # Also index without extension for flexible matching
        stem = str(Path(rel).with_suffix(""))
        rel_paths[stem] = rel
        file_to_rel[f] = rel

    for f in files:
        rel = file_to_rel.get(f)
        if rel is None:
            continue
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        raw_imports = _extract_imports(f, source)
        local_deps: list[str] = []
        ext_deps: list[str] = []

        for imp in raw_imports:
            resolved = _resolve_import(imp, rel, rel_paths, f.suffix)
            if resolved and resolved != rel:
                local_deps.append(resolved)
            else:
                ext_deps.append(imp)

        if local_deps:
            graph.edges[rel] = sorted(set(local_deps))
        if ext_deps:
            graph.external_deps[rel] = sorted(set(ext_deps))

    # Compute clusters using union-find
    graph.clusters = _compute_clusters(graph.edges, set(file_to_rel.values()))
    return graph


def _compute_clusters(
    edges: dict[str, list[str]],
    all_files: set[str],
) -> list[list[str]]:
    """Cluster files into connected components using a union-find algorithm.

    Each file starts in its own component (singleton).  For every directed
    edge ``src → tgt`` in *edges*, ``src`` and ``tgt`` are unioned into the
    same component.  The final components are the weakly-connected groups of
    the directed graph (i.e. direction is ignored for clustering purposes).

    The union-find uses **path compression**: when ``find(x)`` traverses the
    parent chain it performs a one-step shortcut (``parent[x] = parent[parent[x]]``)
    on each node visited, flattening the tree over time.

    Args:
        edges: Adjacency list as stored in :attr:`DependencyGraph.edges`.
            Only edges whose target appears in *all_files* are processed
            (external deps are ignored).
        all_files: Complete set of repository-relative file path strings.
            Used to initialise the union-find structure so that isolated files
            (no edges) still appear as singleton clusters.

    Returns:
        A ``list[list[str]]`` of clusters, each inner list sorted
        alphabetically.  The outer list is sorted by descending cluster size;
        ties are broken by the first filename in the cluster.

    Example::

        edges = {"a.py": ["b.py"], "b.py": ["c.py"]}
        all_files = {"a.py", "b.py", "c.py", "d.py"}
        clusters = _compute_clusters(edges, all_files)
        # [["a.py", "b.py", "c.py"], ["d.py"]]
    """
    parent: dict[str, str] = {f: f for f in all_files}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for src, targets in edges.items():
        for tgt in targets:
            if tgt in all_files:
                union(src, tgt)

    groups: dict[str, list[str]] = {}
    for f in all_files:
        root = find(f)
        groups.setdefault(root, []).append(f)

    return [sorted(g) for g in sorted(groups.values(), key=lambda g: (-len(g), g[0]))]


def format_for_llm_prompt(graph: DependencyGraph, max_edges: int = 150) -> str:
    """Format the dependency graph as a compact string for the LLM planner prompt.

    Renders each source file's internal imports as a single line using the
    ``→`` arrow separator.  Files are ordered by descending number of
    dependencies (most-connected first) so the most architecturally
    significant relationships appear at the top.  The total number of
    individual import relationships printed is capped at *max_edges*; if the
    graph is larger a truncation line is appended.

    Args:
        graph: A :class:`DependencyGraph` as returned by
            :func:`build_dependency_graph`.
        max_edges: Maximum number of individual import edges to include before
            truncating.  Defaults to ``150``.

    Returns:
        A multiline ``str`` suitable for embedding in an LLM prompt, or the
        sentinel string ``"(no internal dependencies detected)"`` when the
        graph has no edges.  Each line has the form::

            src/app.py → src/db.py, src/utils.py

        When truncated, the final line reads::

            ... (42 more edges not shown)

    Example::

        text = format_for_llm_prompt(graph, max_edges=2)
        # "src/app.py → src/db.py, src/utils.py\\n"
        # "... (1 more edges not shown)"
    """
    # Build list of (file, deps) sorted by len(deps) descending
    sorted_entries = sorted(
        graph.edges.items(), key=lambda item: len(item[1]), reverse=True
    )

    lines: list[str] = []
    total_edges = 0
    cutoff_index = None
    for i, (src, deps) in enumerate(sorted_entries):
        if total_edges + len(deps) > max_edges:
            cutoff_index = i
            break
        lines.append(f"{src} → {', '.join(deps)}")
        total_edges += len(deps)

    if cutoff_index is not None:
        remaining = sum(len(d) for _, d in sorted_entries[cutoff_index:])
        lines.append(f"... ({remaining} more edges not shown)")

    if not lines:
        return "(no internal dependencies detected)"
    return "\n".join(lines)


def summarize_page_deps(page_files: list[str], graph: DependencyGraph) -> dict:
    """Summarize dependency info for the set of files belonging to a wiki page.

    Partitions all dependency relationships involving *page_files* into three
    categories: outgoing cross-page imports, incoming cross-page imports, and
    third-party package usage.  Files that are part of *page_files* themselves
    are excluded from all three lists so only *cross-boundary* relationships
    are reported.

    Args:
        page_files: List of repository-relative path strings for all source
            files assigned to a single wiki page.
        graph: The :class:`DependencyGraph` for the full repository.

    Returns:
        A ``dict`` with three keys:

        - ``"depends_on"`` (``list[str]``): Sorted list of repository-relative
          file paths *outside* this page that files in this page import.
        - ``"depended_by"`` (``list[str]``): Sorted list of repository-relative
          file paths *outside* this page that import from files in this page.
        - ``"external_deps"`` (``list[str]``): Sorted list of third-party
          package/module names imported by any file in this page.

    Example::

        page_files = ["src/db.py", "src/models.py"]
        result = summarize_page_deps(page_files, graph)
        # {
        #     "depends_on":    ["src/utils.py"],
        #     "depended_by":   ["src/app.py", "src/routes.py"],
        #     "external_deps": ["pydantic", "sqlalchemy"],
        # }
    """
    page_set = set(page_files)

    depends_on: set[str] = set()
    depended_by: set[str] = set()
    external_deps: set[str] = set()

    for f in page_files:
        # outgoing: files this page imports that are outside the page
        for dep in graph.edges.get(f, []):
            if dep not in page_set:
                depends_on.add(dep)
        # external packages
        external_deps.update(graph.external_deps.get(f, []))

    # incoming: files outside this page that import from files in this page
    for src, targets in graph.edges.items():
        if src not in page_set:
            for t in targets:
                if t in page_set:
                    depended_by.add(src)

    return {
        "depends_on": sorted(depends_on),
        "depended_by": sorted(depended_by),
        "external_deps": sorted(external_deps),
    }
