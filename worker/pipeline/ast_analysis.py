"""Stage 2 of the generation pipeline.

Single-pass Tree-Sitter AST analysis that extracts named entities (functions,
classes, methods, structs, interfaces, etc.) from every supported source file
in a repository.  The results are accumulated into a :class:`FileAnalysis`
object that the wiki-planner and page-generator stages consume.

Supported languages: Python, JavaScript/JSX, TypeScript/TSX, Java, Kotlin, Go,
Rust, C, C++, C#.

Typical usage::

    from pathlib import Path
    from worker.pipeline.ast_analysis import analyze_all_files

    root = Path("/path/to/repo")
    files = list(root.rglob("*.py"))
    analysis = analyze_all_files(root, files)
    print(analysis.to_llm_summary())
    # src/foo.py: 1 classes, 3 functions [MyClass, helper, main, parse]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tree_sitter_c as tsc
import tree_sitter_c_sharp as tscsharp
import tree_sitter_cpp as tscpp
import tree_sitter_go as tsgo
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjavascript
import tree_sitter_kotlin as tskotlin
import tree_sitter_python as tspython
import tree_sitter_rust as tsrust
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser

# Maps a file extension (e.g. ".py", ".ts") to the compiled Tree-Sitter
# Language object for that grammar.  Only extensions listed here will be
# parsed; all others are skipped silently.
SUPPORTED_LANGUAGES: dict[str, Language] = {
    ".py": Language(tspython.language()),
    ".js": Language(tsjavascript.language()),
    ".jsx": Language(tsjavascript.language()),
    ".ts": Language(tstypescript.language_typescript()),
    ".tsx": Language(tstypescript.language_tsx()),
    ".java": Language(tsjava.language()),
    ".kt": Language(tskotlin.language()),
    ".kts": Language(tskotlin.language()),
    ".go": Language(tsgo.language()),
    ".rs": Language(tsrust.language()),
    ".c": Language(tsc.language()),
    ".h": Language(tsc.language()),
    ".cpp": Language(tscpp.language()),
    ".cc": Language(tscpp.language()),
    ".cs": Language(tscsharp.language()),
}

# Tree-Sitter node type strings that correspond to named, documentable
# entities.  The strings must match the grammar's node-type names exactly
# (case-sensitive).  A node whose type appears in this set triggers entity
# extraction in :func:`_extract_entities`.
_ENTITY_TYPES = {
    "function_definition",
    "class_definition",  # Python
    "function_declaration",
    "class_declaration",  # JS/TS/Java/Kotlin
    "method_declaration",
    "method_definition",
    "interface_declaration",
    "object_declaration",  # TS/Kotlin
    "function_item",  # Rust
    "struct_item",
    "impl_item",
    "func_declaration",
    "type_declaration",  # Go
}

# Node types that contain docstrings (language-specific)
_DOCSTRING_CONTAINERS = {
    "expression_statement",  # Python: docstring as first expr in body
}


def analyze_file(path: Path) -> dict[str, Any] | None:
    """Parse a single source file with Tree-Sitter and extract named entities.

    Reads the file as raw bytes (preserving encoding), builds a parse tree,
    and delegates entity extraction to :func:`_extract_entities`.  Returns
    ``None`` for unsupported file types and for files that cannot be read
    (permission errors, broken symlinks, etc.).

    Args:
        path: Absolute or relative :class:`~pathlib.Path` to the source file.
            The file extension determines which Tree-Sitter grammar is used.

    Returns:
        A ``dict`` with two keys, or ``None`` if the file is unsupported or
        unreadable:

        - ``"path"`` (``str``): The string representation of *path*.
        - ``"entities"`` (``list[dict]``): Zero or more entity dicts, each
          with keys ``"type"`` (``"class"`` or ``"function"``), ``"name"``
          (``str``), ``"start_line"`` (``int``), ``"end_line"`` (``int``),
          and optionally ``"signature"`` (``str``) and ``"docstring"``
          (``str``).

    Example::

        from pathlib import Path
        result = analyze_file(Path("src/app.py"))
        # result == {
        #     "path": "src/app.py",
        #     "entities": [
        #         {
        #             "type": "class",
        #             "name": "App",
        #             "start_line": 5,
        #             "end_line": 42,
        #             "docstring": "Main application class.",
        #         },
        #         {
        #             "type": "function",
        #             "name": "run",
        #             "start_line": 10,
        #             "end_line": 20,
        #             "signature": "run(self, port: int = 8000) -> None",
        #         },
        #     ],
        # }
    """
    lang = SUPPORTED_LANGUAGES.get(path.suffix.lower())
    if lang is None:
        return None
    try:
        source = path.read_bytes()
    except (OSError, PermissionError):
        return None

    parser = Parser(lang)
    tree = parser.parse(source)
    entities = _extract_entities(tree.root_node, source)
    return {"path": str(path), "entities": entities}


def _get_signature(node: Any, source: bytes) -> str | None:
    """Extract a human-readable function/method signature from an AST node.

    Attempts to read the ``parameters`` (or ``formal_parameters``) child
    field of *node*, then optionally appends a return-type annotation if the
    node has a ``return_type`` or ``type`` field.

    Args:
        node: A Tree-Sitter ``Node`` object representing a function or method
            definition.  The node must expose ``child_by_field_name()``.
        source: The raw UTF-8 encoded source bytes of the file being parsed.
            Used to decode node text slices.

    Returns:
        A signature string of the form ``"name(params) -> return_type"`` when
        all fields are present, ``"name(params)"`` when there is no return
        annotation, or ``None`` if no parameter node can be found.

    Example::

        # For a Python node representing `def greet(name: str) -> str:`
        sig = _get_signature(func_node, source_bytes)
        # sig == "greet(name: str) -> str"

        # For a node with no parameters field (e.g. a bare `class Foo:`)
        sig = _get_signature(class_node, source_bytes)
        # sig == None
    """
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        params_node = node.child_by_field_name("formal_parameters")
    if params_node is None:
        return None

    name_node = node.child_by_field_name("name")
    name = (
        name_node.text.decode("utf-8", errors="replace") if name_node else "<anonymous>"
    )
    params = params_node.text.decode("utf-8", errors="replace")

    # Include return type if present
    ret_node = node.child_by_field_name("return_type") or node.child_by_field_name(
        "type"
    )
    ret = ""
    if ret_node:
        ret = f" -> {ret_node.text.decode('utf-8', errors='replace')}"

    return f"{name}{params}{ret}"


def _get_docstring(node: Any, source: bytes) -> str | None:
    """Extract the docstring or leading comment for a class or function node.

    Supports three strategies in order of precedence:

    1. **Python block docstring** — looks for a ``string`` literal as the
       first expression inside a ``block`` body, strips triple-quote delimiters
       (``\"\"\"`` or ``'''``), and returns up to 300 characters.
    2. **JS/TS/Java/Kotlin/C# block comment** — reads the immediately
       preceding named sibling if it is a ``comment``, ``block_comment``, or
       ``line_comment`` node, strips ``/**``, ``*/``, ``/*``, ``//``, and
       ``#`` markers, collapses whitespace, and returns up to 300 characters.
    3. **Rust doc comment** — reads the preceding sibling if its node type
       contains ``"doc_comment"``, strips ``///`` and ``//!`` prefixes, and
       returns up to 300 characters.

    Returns ``None`` when no docstring or comment can be found.

    Args:
        node: A Tree-Sitter ``Node`` for a function, class, struct, or
            similar entity.
        source: The raw UTF-8 encoded source bytes of the file (currently
            unused but kept for API consistency with :func:`_get_signature`).

    Returns:
        A cleaned docstring string of at most 300 characters, or ``None`` if
        no docstring is present.

    Example::

        # Python function with triple-quoted docstring
        doc = _get_docstring(func_node, source_bytes)
        # doc == "Compute the factorial of n."

        # TypeScript method preceded by a JSDoc block comment
        doc = _get_docstring(method_node, source_bytes)
        # doc == "Returns the current user session."
    """
    # Python: first string literal in body block
    body = node.child_by_field_name("body")
    if body and body.type == "block":
        for child in body.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "string":
                        raw = sub.text.decode("utf-8", errors="replace")
                        # Strip triple quotes
                        for q in ('"""', "'''"):
                            if raw.startswith(q) and raw.endswith(q):
                                raw = raw[3:-3]
                                break
                        return raw.strip()[:300]
                break  # Only check first statement

    # JS/TS/Java/Kotlin/C#: preceding comment node
    prev = node.prev_named_sibling
    if prev and prev.type in ("comment", "block_comment", "line_comment"):
        raw = prev.text.decode("utf-8", errors="replace")
        # Strip comment markers
        for marker in ("/**", "*/", "/*", "//", "#"):
            raw = raw.replace(marker, "")
        cleaned = " ".join(raw.split())
        return cleaned.strip()[:300] if cleaned.strip() else None

    # Rust: preceding line_comment or outer_doc_comment
    if prev and "doc_comment" in prev.type:
        raw = prev.text.decode("utf-8", errors="replace")
        raw = raw.replace("///", "").replace("//!", "")
        return raw.strip()[:300]

    return None


def _extract_entities(node: Any, source: bytes) -> list[dict[str, Any]]:
    """Recursively walk a Tree-Sitter node tree and collect named entities.

    Performs a depth-first traversal of the AST rooted at *node*.  Whenever a
    node whose ``type`` is in :data:`_ENTITY_TYPES` is encountered, an entity
    dict is appended to the results before recursing into the node's children.
    This means nested entities (e.g. a method inside a class) are captured
    individually — both the class and each of its methods appear in the
    returned list.

    The ``"type"`` field of each entity dict is classified as ``"class"`` if
    any of the keywords ``class``, ``struct``, ``interface``, ``object``, or
    ``impl`` appear in the node type string; otherwise it is ``"function"``.

    Args:
        node: The Tree-Sitter ``Node`` to start traversal from.  On the
            initial call this should be ``tree.root_node``; recursive calls
            pass child nodes.
        source: The raw UTF-8 encoded source bytes of the file being parsed,
            forwarded to :func:`_get_signature` and :func:`_get_docstring`.

    Returns:
        A flat ``list[dict[str, Any]]`` of entity dicts.  Each dict always
        contains ``"type"``, ``"name"``, ``"start_line"``, and ``"end_line"``
        keys.  ``"signature"`` and ``"docstring"`` keys are present only when
        the respective helpers return a non-``None`` value.

    Example::

        entities = _extract_entities(tree.root_node, source_bytes)
        # [
        #     {"type": "class",    "name": "Parser", "start_line": 1,
        #      "end_line": 50, "docstring": "..."},
        #     {"type": "function", "name": "__init__", "start_line": 5,
        #      "end_line": 10, "signature": "__init__(self)"},
        # ]
    """
    results = []
    if node.type in _ENTITY_TYPES:
        name_node = node.child_by_field_name("name")
        name = (
            name_node.text.decode("utf-8", errors="replace")
            if name_node
            else "<anonymous>"
        )
        kind = (
            "class"
            if any(
                kw in node.type
                for kw in ["class", "struct", "interface", "object", "impl"]
            )
            else "function"
        )
        entity: dict[str, Any] = {
            "type": kind,
            "name": name,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
        }
        sig = _get_signature(node, source)
        if sig:
            entity["signature"] = sig
        doc = _get_docstring(node, source)
        if doc:
            entity["docstring"] = doc
        results.append(entity)
    for child in node.children:
        results.extend(_extract_entities(child, source))
    return results


@dataclass
class FileInfo:
    """Lightweight summary of a single source file produced by Stage 2.

    Instances are created by :func:`analyze_all_files` and stored in
    :attr:`FileAnalysis.files`.  They carry just enough information for the
    wiki-planner LLM prompt without transmitting the full AST.

    Attributes:
        rel_path: Repository-relative POSIX path string (e.g.
            ``"src/core/parser.py"``).
        entities: Full list of entity dicts as returned by
            :func:`_extract_entities`.  Each dict has at minimum ``"type"``,
            ``"name"``, ``"start_line"``, and ``"end_line"`` keys.
        class_count: Pre-computed count of entities with ``type == "class"``
            for fast summary rendering.
        function_count: Pre-computed count of entities with
            ``type == "function"`` for fast summary rendering.
        summary: Comma-joined string of the first 20 entity names (e.g.
            ``"MyClass, helper, main"``).  Used as a compact fingerprint in
            the LLM planner prompt.
    """

    rel_path: str
    entities: list[dict] = field(default_factory=list)
    class_count: int = 0
    function_count: int = 0
    summary: str = ""  # comma-joined top-20 entity names


@dataclass
class FileAnalysis:
    """Aggregated AST analysis results for all source files in a repository.

    Produced by :func:`analyze_all_files` and passed downstream to the wiki
    planner (Stage 5) and page generator (Stage 6).

    Attributes:
        files: Mapping from repository-relative path string to the
            corresponding :class:`FileInfo`.  Keys use forward slashes on all
            platforms (e.g. ``"src/core/parser.py"``).
    """

    files: dict[str, FileInfo] = field(default_factory=dict)  # keyed by rel_path

    def to_llm_summary(self, max_files: int = 200) -> str:
        """Return compact per-file summaries suitable for an LLM planner prompt.

        Files are sorted alphabetically by relative path, then truncated to
        *max_files* entries.  Files with no recognised entities are shown with
        the placeholder ``"(no named entities)"``.  If the total count exceeds
        *max_files*, a trailing count line is appended.

        Args:
            max_files: Maximum number of files to include in the output before
                adding a truncation line.  Defaults to ``200``.

        Returns:
            A multiline ``str`` where each line describes one file::

                src/app.py: 1 classes, 3 functions [App, run, main, helper]
                src/utils.py: 0 classes, 2 functions [parse_url, slugify]
                ... and 47 more files

        Example::

            analysis = analyze_all_files(root, files)
            summary = analysis.to_llm_summary(max_files=3)
            # "api/routes.py: 0 classes, 2 functions [index, health]\\n"
            # "src/app.py: 1 classes, 3 functions [App, run, main]\\n"
            # "... and 12 more files"
        """
        lines = []
        sorted_keys = sorted(self.files.keys())
        truncated = sorted_keys[:max_files]
        remaining = len(sorted_keys) - len(truncated)

        for rel_path in truncated:
            info = self.files[rel_path]
            if not info.entities:
                lines.append(f"{rel_path}: (no named entities)")
            else:
                lines.append(
                    f"{rel_path}: {info.class_count} classes,"
                    f" {info.function_count} functions [{info.summary}]"
                )

        if remaining > 0:
            lines.append(f"... and {remaining} more files")

        return "\n".join(lines)


def analyze_all_files(root: Path, files: list[Path]) -> FileAnalysis:
    """Run single-pass AST analysis over a collection of source files.

    For each file in *files*, calls :func:`analyze_file` to obtain entities,
    then builds a :class:`FileInfo` summary.  Files that are unsupported or
    unreadable produce a :class:`FileInfo` with empty ``entities``.

    The function does **not** recurse into directories; *files* must already
    be an explicit, filtered list (typically produced by the ingestion stage).

    Args:
        root: The repository root :class:`~pathlib.Path`.  Used to compute
            repository-relative paths via :meth:`~pathlib.Path.relative_to`.
            If a file cannot be made relative to *root* (e.g. a symlink
            outside the tree), its absolute path string is used as the key.
        files: List of :class:`~pathlib.Path` objects pointing to source files
            to analyse.  May be absolute or relative; typically absolute paths
            produced by ``root.rglob("*")``.

    Returns:
        A :class:`FileAnalysis` whose ``files`` dict is keyed by
        repository-relative path strings (forward slashes) and whose values
        are populated :class:`FileInfo` instances.

    Example::

        from pathlib import Path
        root = Path("/repos/myproject")
        src_files = list(root.rglob("*.py"))
        analysis = analyze_all_files(root, src_files)
        # analysis.files["src/app.py"].class_count  -> 1
        # analysis.files["src/app.py"].function_count -> 3
        # analysis.files["src/app.py"].summary -> "App, run, main, parse"
        print(analysis.to_llm_summary())
    """
    result: dict[str, FileInfo] = {}

    for f in files:
        try:
            rel_path = str(f.relative_to(root))
        except ValueError:
            rel_path = str(f)

        analysis = analyze_file(f)
        entities: list[dict] = analysis["entities"] if analysis else []

        class_count = sum(1 for e in entities if e["type"] == "class")
        function_count = sum(1 for e in entities if e["type"] == "function")
        summary = ", ".join(e["name"] for e in entities[:20])

        result[rel_path] = FileInfo(
            rel_path=rel_path,
            entities=entities,
            class_count=class_count,
            function_count=function_count,
            summary=summary,
        )

    return FileAnalysis(files=result)
