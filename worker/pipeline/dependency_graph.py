"""Extract import/dependency relationships between source files and cluster modules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DependencyGraph:
    """Directed graph of file-level import relationships."""

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
    """Extract raw import strings from a source file."""
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
    """Try to resolve a raw import string to a known file in the repo.

    Returns the relative file path if found, else None (external dependency).
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
    """Build a dependency graph from source files under root."""
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
    """Cluster files by connectivity using union-find."""
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
    """Format dependency graph as a compact string for the LLM planner prompt.

    Shows file → [imports] relationships, capped at max_edges total edges,
    sorted by number of dependencies (most connected files first).
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
    """Summarize dependency info for a page's set of files.

    Returns:
        {
            "depends_on": [files outside this page that this page imports],
            "depended_by": [files outside this page that import from this page],
            "external_deps": [external package names imported by files in this page],
        }
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
