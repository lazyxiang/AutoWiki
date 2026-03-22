from __future__ import annotations
from pathlib import Path
from typing import Any

import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
import tree_sitter_java as tsjava
import tree_sitter_go as tsgo
import tree_sitter_rust as tsrust
import tree_sitter_c as tsc
import tree_sitter_cpp as tscpp
import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Parser

SUPPORTED_LANGUAGES: dict[str, Language] = {
    ".py":   Language(tspython.language()),
    ".js":   Language(tsjavascript.language()),
    ".jsx":  Language(tsjavascript.language()),
    ".ts":   Language(tstypescript.language_typescript()),
    ".tsx":  Language(tstypescript.language_tsx()),
    ".java": Language(tsjava.language()),
    ".go":   Language(tsgo.language()),
    ".rs":   Language(tsrust.language()),
    ".c":    Language(tsc.language()),
    ".h":    Language(tsc.language()),
    ".cpp":  Language(tscpp.language()),
    ".cc":   Language(tscpp.language()),
    ".cs":   Language(tscsharp.language()),
}

# Tree-Sitter node types that represent named entities
_ENTITY_TYPES = {
    "function_definition", "class_definition",       # Python
    "function_declaration", "class_declaration",     # JS/TS/Java
    "method_declaration", "method_definition",
    "function_item",                                 # Rust
    "struct_item", "impl_item",
    "func_declaration", "type_declaration",          # Go
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


def _extract_entities(node: Any, source: bytes) -> list[dict[str, Any]]:
    results = []
    if node.type in _ENTITY_TYPES:
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode("utf-8", errors="replace") if name_node else "<anonymous>"
        kind = "class" if "class" in node.type or "struct" in node.type else "function"
        results.append({
            "type": kind,
            "name": name,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
        })
    for child in node.children:
        results.extend(_extract_entities(child, source))
    return results


def build_module_tree(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    """Group files into modules by top-level directory under root."""
    modules: dict[str, list[Path]] = {}
    for f in files:
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        module_path = parts[0] if len(parts) > 1 else "."
        modules.setdefault(module_path, []).append(f)

    return [{"path": mod, "files": [str(f) for f in fs]} for mod, fs in sorted(modules.items())]
