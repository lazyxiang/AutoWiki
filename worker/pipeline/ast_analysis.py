from __future__ import annotations

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

    return [
        {"path": mod, "files": [str(f) for f in fs]}
        for mod, fs in sorted(modules.items())
    ]


def build_enhanced_module_tree(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    """Build a module tree enriched with entity summaries for wiki planning."""
    modules: dict[str, list[Path]] = {}
    for f in files:
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        module_path = parts[0] if len(parts) > 1 else "."
        modules.setdefault(module_path, []).append(f)

    result = []
    for mod, mod_files in sorted(modules.items()):
        all_entities: list[dict[str, Any]] = []
        rel_files: list[str] = []

        for f in mod_files:
            try:
                rel_files.append(str(f.relative_to(root)))
            except ValueError:
                rel_files.append(str(f))

            analysis = analyze_file(f)
            if analysis:
                all_entities.extend(analysis["entities"])

        classes = [e for e in all_entities if e["type"] == "class"]
        functions = [e for e in all_entities if e["type"] == "function"]

        # Build a concise summary of key entities
        top_entities = [e["name"] for e in all_entities[:20]]
        summary = ", ".join(top_entities) if top_entities else "(no named entities)"

        result.append(
            {
                "path": mod,
                "files": rel_files,
                "file_count": len(mod_files),
                "class_count": len(classes),
                "function_count": len(functions),
                "classes": [
                    {
                        "name": c["name"],
                        "signature": c.get("signature"),
                        "docstring": c.get("docstring"),
                    }
                    for c in classes[:10]
                ],
                "functions": [
                    {
                        "name": f["name"],
                        "signature": f.get("signature"),
                        "docstring": f.get("docstring"),
                    }
                    for f in functions[:15]
                ],
                "summary": summary,
            }
        )

    return result
