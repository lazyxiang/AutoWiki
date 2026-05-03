"""Build a deterministic repository analysis index for retrieval."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from worker.pipeline.ast_analysis import FileAnalysis
from worker.pipeline.dependency_graph import DependencyGraph
from worker.utils.tokenize import tokenize_text

_README_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

REPO_INDEX_VERSION = 2
_DIRECTORY_TREE_HARD_CAP_TOKENS = 25_000
_README_SECTION_BODY_CAP = 800
_README_SECTIONS_TOTAL_TOKEN_CAP = 10_000
_HUB_MAX = 20
_HUB_PURPOSE_CHAR_CAP = 120

_DIRECTORY_EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".next",
    ".turbo",
    ".venv",
    "venv",
    ".cache",
    ".pytest_cache",
    "coverage",
    ".mypy_cache",
    ".ruff_cache",
}
_DIRECTORY_EXCLUDED_SUFFIXES = (".pyc", ".lock", ".min.js")
_DIRECTORY_EXCLUDED_FILES = {"package-lock.json", "yarn.lock"}

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


def build_repo_index(
    *,
    root: Path,
    files: list[Path],
    file_analysis: FileAnalysis,
    dep_graph: DependencyGraph,
    readme: str | None,
) -> dict[str, Any]:
    """Return a deterministic repository analysis index."""
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
    hub_paths = {hub["path"] for hub in hub_modules}

    return {
        "index_version": REPO_INDEX_VERSION,
        "directory_tree": _build_directory_tree_with_degradation(
            rel_paths, hub_paths=hub_paths
        ),
        "hub_modules": hub_modules,
        "readme_headings": _extract_readme_headings(readme),
        "readme_sections": _extract_readme_sections(readme),
        "files": files_index,
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


def _is_directory_tree_excluded(rel_path: str) -> bool:
    parts = _normalize_rel_path(rel_path).split("/")
    if any(part in _DIRECTORY_EXCLUDED_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in _DIRECTORY_EXCLUDED_DIRS:
        return True
    if name in _DIRECTORY_EXCLUDED_FILES:
        return True
    return name.endswith(_DIRECTORY_EXCLUDED_SUFFIXES)


def _build_directory_tree(rel_paths: list[str]) -> str:
    kept = sorted(
        {
            _normalize_rel_path(path)
            for path in rel_paths
            if path and not _is_directory_tree_excluded(path)
        }
    )
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
        normalized = _normalize_rel_path(path)
        if _is_directory_tree_excluded(normalized):
            continue
        if normalized.count("/") <= 3 or normalized in hub_paths:
            kept.append(normalized)

    degraded = _build_directory_tree(kept)
    if _approx_tokens(degraded) <= _DIRECTORY_TREE_HARD_CAP_TOKENS:
        return degraded

    by_top: dict[str, list[str]] = {}
    for path in kept:
        by_top.setdefault(path.split("/", 1)[0], []).append(path)
    hub_tops = {path.split("/", 1)[0] for path in hub_paths if path}
    sorted_tops = sorted(
        by_top.items(),
        key=lambda kv: (kv[0] not in hub_tops, -len(kv[1])),
    )
    trimmed: list[str] = []
    for _top, members in sorted_tops:
        previous = list(trimmed)
        trimmed.extend(members)
        if (
            _approx_tokens(_build_directory_tree(trimmed))
            > _DIRECTORY_TREE_HARD_CAP_TOKENS
        ):
            trimmed = previous
            continue
    return _build_directory_tree(trimmed)


def _extract_readme_headings(readme: str | None) -> list[str]:
    if not readme:
        return []
    return [match.group(2).strip() for match in _README_HEADING_RE.finditer(readme)]


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
        cumulative_tokens += _approx_tokens(heading) + _approx_tokens(body)
        if cumulative_tokens > _README_SECTIONS_TOTAL_TOKEN_CAP:
            break
        sections.append({"heading": heading, "body": body})
    return sections


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
        sentence = sentence[:_HUB_PURPOSE_CHAR_CAP].rstrip() + "..."
    return sentence


def _compute_hub_modules(files: dict[str, dict]) -> list[dict]:
    ranked: list[tuple[int, str, dict]] = []
    for path, entry in files.items():
        in_degree = len(entry.get("imported_by") or [])
        if in_degree >= 2:
            ranked.append((in_degree, path, entry))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [
        {
            "path": path,
            "in_degree": in_degree,
            "purpose": _first_sentence(entry.get("module_docstring")),
        }
        for in_degree, path, entry in ranked[:_HUB_MAX]
    ]


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
    extras = info.extras if info is not None else {}

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
        "call_sites": _normalize_symbol_touchpoints(
            rel_path, extras.get("call_sites", [])
        ),
        "exception_touchpoints": _normalize_symbol_touchpoints(
            rel_path, extras.get("exception_touchpoints", [])
        ),
        "config_touchpoints": extras.get("config_touchpoints", []),
    }


def _module_docstring_for(info: Any) -> str | None:
    if info is None:
        return None
    module_docstring = getattr(info, "module_docstring", None)
    if module_docstring:
        return module_docstring
    for entity in getattr(info, "entities", []) or []:
        if entity.get("type") == "module" and entity.get("docstring"):
            return entity["docstring"]
    return None


def _normalize_symbol_touchpoints(
    rel_path: str, touchpoints: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for touchpoint in touchpoints:
        item = dict(touchpoint)
        for key in ("caller_symbol_path", "symbol_path"):
            value = item.get(key)
            if value and "." not in value and value != "module":
                item[key] = _symbol_path(rel_path, value)
            elif value == "module":
                item[key] = _symbol_path(rel_path, "")
        normalized.append(item)
    return normalized


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
    tokens = set(tokenize_text(rel_path, min_ascii_len=2))
    for entity in entities:
        tokens |= tokenize_text(entity.get("name", ""), min_ascii_len=2)
        tokens |= tokenize_text(entity.get("symbol_path", ""), min_ascii_len=2)
        if entity.get("signature"):
            tokens |= tokenize_text(entity["signature"], min_ascii_len=2)
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
