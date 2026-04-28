"""Build a deterministic repository analysis index for fast reports."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from worker.pipeline.ast_analysis import FileAnalysis
from worker.pipeline.dependency_graph import DependencyGraph

_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_CASE_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_README_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

_CONFIG_BASENAMES = {
    ".env",
    ".env.example",
    ".gitignore",
    ".prettierignore",
    ".prettierrc",
    "docker-compose.yaml",
    "docker-compose.yml",
    "makefile",
    "package-lock.json",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "tsconfig.json",
    "yarn.lock",
}
_CONFIG_STEMS = {
    "config",
    "configs",
    "configuration",
    "configure",
    "settings",
}


def build_fast_report_index(
    *,
    root: Path,
    files: list[Path],
    file_analysis: FileAnalysis,
    dep_graph: DependencyGraph,
    readme: str | None,
) -> dict[str, Any]:
    """Return a deterministic fast-report analysis index."""
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

    return {
        "top_level_entries": _top_level_entries(root, rel_paths),
        "readme_headings": _extract_readme_headings(readme),
        "files": {
            rel_path: _build_file_entry(
                rel_path=rel_path,
                normalized_file_info=normalized_file_info,
                normalized_edges=normalized_edges,
                normalized_external_deps=normalized_external_deps,
                imported_by=imported_by,
            )
            for rel_path in rel_paths
        },
    }


def _collect_rel_paths(
    root: Path, files: list[Path], file_analysis: FileAnalysis
) -> list[str]:
    rel_paths = {_normalize_rel_path(rel_path) for rel_path in file_analysis.files}
    for path in files:
        try:
            rel_paths.add(_normalize_rel_path(path.relative_to(root).as_posix()))
        except ValueError:
            rel_paths.add(_normalize_rel_path(path.as_posix()))
    return sorted(rel_paths)


def _build_imported_by(dep_graph: DependencyGraph) -> dict[str, list[str]]:
    reverse: dict[str, set[str]] = {}
    for source, targets in dep_graph.edges.items():
        normalized_source = _normalize_rel_path(source)
        reverse.setdefault(normalized_source, set())
        for target in targets:
            normalized_target = _normalize_rel_path(target)
            reverse.setdefault(normalized_target, set()).add(normalized_source)
    return {path: sorted(sources) for path, sources in reverse.items()}


def _top_level_entries(root: Path, rel_paths: list[str]) -> list[str]:
    if root.exists():
        entries = sorted(p.name for p in root.iterdir() if p.name != ".git")
        if entries:
            return entries
    return sorted({rel_path.split("/", 1)[0] for rel_path in rel_paths if rel_path})


def _extract_readme_headings(readme: str | None) -> list[str]:
    if not readme:
        return []
    return [match.group(2).strip() for match in _README_HEADING_RE.finditer(readme)]


def _build_file_entry(
    *,
    rel_path: str,
    normalized_file_info: dict[str, Any],
    normalized_edges: dict[str, list[str]],
    normalized_external_deps: dict[str, list[str]],
    imported_by: dict[str, list[str]],
) -> dict[str, Any]:
    rel_path = _normalize_rel_path(rel_path)
    info = normalized_file_info.get(rel_path)
    entities = info.entities if info is not None else []
    normalized_entities = [_normalize_entity(rel_path, entity) for entity in entities]

    return {
        "path": rel_path,
        "tokens": _file_tokens(rel_path, normalized_entities),
        "imports": normalized_edges.get(rel_path, []),
        "imported_by": imported_by.get(rel_path, []),
        "external_deps": normalized_external_deps.get(rel_path, []),
        "entities": normalized_entities,
        "is_test": _is_test_file(rel_path),
        "is_config": _is_config_file(rel_path),
    }


def _normalize_entity(rel_path: str, entity: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "name": entity.get("name", ""),
        "type": entity.get("type", ""),
        "start_line": entity.get("start_line"),
        "end_line": entity.get("end_line"),
        "symbol_path": _symbol_path(rel_path, entity.get("name", "")),
    }
    if entity.get("signature"):
        normalized["signature"] = entity["signature"]
    if entity.get("docstring"):
        normalized["docstring"] = entity["docstring"]
    return normalized


def _symbol_path(rel_path: str, entity_name: str) -> str:
    module_path = _normalize_rel_path(rel_path)
    if module_path.endswith("/__init__.py"):
        module_path = module_path[: -len("/__init__.py")]
    else:
        module_path = module_path.rsplit(".", 1)[0]
    module_path = module_path.replace("/", ".")
    return f"{module_path}.{entity_name}" if entity_name else module_path


def _file_tokens(rel_path: str, entities: list[dict[str, Any]]) -> list[str]:
    rel_path = _normalize_rel_path(rel_path)
    tokens: list[str] = []
    seen: set[str] = set()

    def _add_token(token: str) -> None:
        normalized = token.strip().lower()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        tokens.append(normalized)

    def _add_text(text: str) -> None:
        for part in _TOKEN_SPLIT_RE.split(text):
            if not part:
                continue
            for camel_part in _CAMEL_CASE_RE.split(part):
                _add_token(camel_part)

    _add_text(rel_path.replace("/", " "))
    for entity in entities:
        _add_text(entity.get("name", ""))
        _add_text(entity.get("symbol_path", ""))
        if entity.get("signature"):
            _add_text(entity["signature"])
    return sorted(tokens)


def _is_test_file(rel_path: str) -> bool:
    path = PurePosixPath(_normalize_rel_path(rel_path))
    lower_parts = [part.lower() for part in path.parts]
    stem = path.stem.lower()
    return (
        "tests" in lower_parts
        or "__tests__" in lower_parts
        or stem.startswith("test_")
        or stem.endswith("_test")
    )


def _is_config_file(rel_path: str) -> bool:
    path = PurePosixPath(_normalize_rel_path(rel_path))
    name = path.name.lower()
    stem = path.stem.lower()
    return (
        name in _CONFIG_BASENAMES
        or stem in _CONFIG_STEMS
        or any(part.lower() in _CONFIG_STEMS for part in path.parts[:-1])
    )


def _normalize_rel_path(rel_path: str) -> str:
    return rel_path.replace("\\", "/")
