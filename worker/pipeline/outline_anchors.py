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
    leading docstring, <= 500 chars.
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

    # Build a nested dict: {dir/: {subdir/: ... | filename: None}}
    root: dict = {}
    for rel in files:
        parts = rel.split("/")
        node = root
        for i, part in enumerate(parts):
            is_file = i == len(parts) - 1
            if is_file:
                node[part] = None
            else:
                node = node.setdefault(part + "/", {})

    def _count(node: dict) -> int:
        total = 0
        for child in node.values():
            total += 1 if child is None else _count(child)
        return total

    lines: list[str] = []

    def _emit(node: dict, depth: int, prefix: str) -> None:
        if depth > max_depth:
            return
        dirs = sorted(k for k, v in node.items() if isinstance(v, dict))
        files_here = sorted(k for k, v in node.items() if v is None)

        # Synthetic (root) bucket for top-level files only at depth 1.
        if depth == 1 and files_here:
            lines.append(f"(root) ({len(files_here)})")

        for d in dirs:
            sub = node[d]
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
        lines = [f"{p.package}: {p.docstring}" for p in package_docstrings]
        sections.append("## Package docstrings\n" + "\n".join(lines))
    if readme_sections:
        lines = [f"{lvl} {title}" for lvl, title in readme_sections]
        sections.append("## README sections\n" + "\n".join(lines))
    return "\n\n".join(sections)
