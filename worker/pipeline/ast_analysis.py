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

# Tree-Sitter node types that represent named entities
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
    """Parse a file with Tree-Sitter. Returns entity list or None if unsupported."""
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
    """Extract function/method signature (parameter list)."""
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
    """Extract docstring/comment for a class or function node."""
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
    rel_path: str
    entities: list[dict] = field(default_factory=list)
    class_count: int = 0
    function_count: int = 0
    summary: str = ""  # comma-joined top-20 entity names


@dataclass
class FileAnalysis:
    files: dict[str, FileInfo] = field(default_factory=dict)  # keyed by rel_path

    def to_llm_summary(self, max_files: int = 200) -> str:
        """Return compact per-file summaries for LLM planner prompt.

        Format for each file (truncated to max_files):
        - path/to/file.py: 3 classes, 7 functions [ClassName, func_one, func_two, ...]
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
    """Single-pass AST analysis of all files. Returns FileAnalysis."""
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
