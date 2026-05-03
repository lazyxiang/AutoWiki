"""Domain-agnostic repository retrieval scoring and expansion primitives."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from worker.utils.tokenize import tokenize_text

Tokenizer = Callable[[str], set[str]]


def _tokenize(text: str) -> set[str]:
    return tokenize_text(text, min_ascii_len=2)


class RetrievalProfileLike(Protocol):
    depth: int
    result_limit: int
    line_cap: int
    slices_per_file: int


@dataclass(slots=True)
class RankedFile:
    path: str
    score: float
    matched_entity: dict[str, Any] | None
    matched_entities: list[ScoredEntity] | None = None
    exact_focus_match: bool = False


@dataclass(slots=True)
class ScoredEntity:
    entity: dict[str, Any] | None
    score: float


@dataclass(slots=True)
class SliceCandidate:
    citation_id: str
    file_path: str
    start_line: int
    end_line: int
    code: str
    symbol_path: str | None
    label: str
    score: float
    reason: str
    full_start: int
    full_end: int
    truncated_lines: int = 0


_EXPANSION_PROFILES: dict[str, Any] = {}


def register_expansion_profile(name: str, config: Any) -> None:
    key = name.strip().lower()
    if not key:
        raise ValueError("Expansion profile name must be non-empty")
    _EXPANSION_PROFILES[key] = config


def expansion_graph_for(name: str) -> Any:
    return _EXPANSION_PROFILES[name.strip().lower()]


def score_file_for_query(
    path: str,
    entry: dict[str, Any],
    query_tokens: set[str],
    focus_hints: list[str],
    profile: RetrievalProfileLike,
    *,
    tokenizer: Tokenizer = _tokenize,
) -> RankedFile:
    lower_path = path.lower()
    file_tokens = set(entry.get("tokens") or []) | tokenizer(path)
    best_entity = _select_primary_entity(entry)
    exact_focus_match = False

    score = float(len(query_tokens & file_tokens) * 2)
    if entry.get("imports"):
        score += 0.5
    if entry.get("imported_by"):
        score += 0.5

    for hint in focus_hints:
        if hint == lower_path:
            score += 12
            exact_focus_match = True
        elif hint.replace(".", "/") in lower_path:
            score += 6

    scored_entities: list[ScoredEntity] = []
    for entity in entry.get("entities") or []:
        candidate_score = float(
            len(query_tokens & _entity_tokens(entity, tokenizer=tokenizer)) * 2
            + _focus_hint_score(entity, lower_path, focus_hints)
        )
        scored_entities.append(ScoredEntity(entity=entity, score=candidate_score))

    scored_entities.sort(
        key=lambda item: (
            -item.score,
            int(item.entity.get("start_line") or 0) if item.entity else 0,
            str(item.entity.get("name", "")) if item.entity else "",
        )
    )
    selected_entities: list[ScoredEntity] = []
    if scored_entities and scored_entities[0].score > 0:
        threshold = scored_entities[0].score * 0.5
        selected_entities = [
            item
            for item in scored_entities[: profile.slices_per_file]
            if item.score >= threshold
        ]

    if selected_entities:
        best_entity = selected_entities[0].entity
        score += sum(item.score for item in selected_entities)
        exact_focus_match = exact_focus_match or any(
            any(
                hint == str(item.entity.get("symbol_path", "")).lower()
                for hint in focus_hints
            )
            for item in selected_entities
            if item.entity
        )

    return RankedFile(
        path=path,
        score=score,
        matched_entity=best_entity,
        matched_entities=selected_entities,
        exact_focus_match=exact_focus_match,
    )


def expand_candidate_paths(
    files: dict[str, dict[str, Any]],
    seeds: list[RankedFile],
    ranked: list[RankedFile] | None = None,
    *,
    graph: Any,
    query_tokens: set[str],
    profile: RetrievalProfileLike,
    allow_config_files: bool = False,
    allow_test_files: bool = False,
    tokenizer: Tokenizer = _tokenize,
) -> list[RankedFile]:
    if not seeds:
        return []

    primary_graph, fallback_graph = _graph_parts(graph)
    ranked_lookup = {candidate.path: candidate for candidate in (ranked or seeds)}
    selected: list[RankedFile] = []
    seen: set[str] = set()
    queue = deque((candidate, 0) for candidate in seeds)

    for candidate in seeds:
        selected.append(candidate)
        seen.add(candidate.path)

    while queue and len(selected) < profile.result_limit:
        candidate, depth = queue.popleft()
        if depth >= profile.depth:
            continue
        neighbors = neighbors_for_graph(
            files,
            candidate.path,
            primary_graph,
            query_tokens,
            allow_test_files=allow_test_files,
            tokenizer=tokenizer,
        )
        if not neighbors and fallback_graph:
            neighbors = neighbors_for_graph(
                files,
                candidate.path,
                fallback_graph,
                query_tokens,
                allow_test_files=allow_test_files,
                tokenizer=tokenizer,
            )
        for neighbor_path in neighbors:
            if neighbor_path in seen or neighbor_path not in files:
                continue
            neighbor_entry = files[neighbor_path]
            if _is_low_signal_entry(
                neighbor_entry, [], allow_config_files, allow_test_files
            ):
                continue
            neighbor = ranked_lookup.get(neighbor_path)
            if neighbor is None:
                neighbor = RankedFile(
                    path=neighbor_path,
                    score=max(candidate.score - (depth + 1), 0.1),
                    matched_entity=_select_primary_entity(neighbor_entry),
                    matched_entities=_default_scored_entities(neighbor_entry),
                    exact_focus_match=False,
                )
                ranked_lookup[neighbor.path] = neighbor
            selected.append(neighbor)
            seen.add(neighbor_path)
            queue.append((neighbor, depth + 1))
            if len(selected) >= profile.result_limit:
                break

    return selected


def neighbors_for_graph(
    files: dict[str, dict[str, Any]],
    path: str,
    graph: str,
    query_tokens: set[str] | None = None,
    *,
    allow_test_files: bool = False,
    tokenizer: Tokenizer = _tokenize,
) -> list[str]:
    query_tokens = query_tokens or set()
    entry = files.get(path, {})
    if graph == "imports_and_imported_by":
        return _dedupe_paths(
            list(entry.get("imports") or []) + list(entry.get("imported_by") or [])
        )
    if graph == "imports":
        return _dedupe_paths(list(entry.get("imports") or []))
    if graph == "call_sites":
        return _call_site_neighbors(files, path)
    if graph == "exception_touchpoints":
        return _exception_touchpoint_neighbors(
            files, path, allow_test_files, tokenizer=tokenizer
        )
    if graph == "config_touchpoints":
        return _config_touchpoint_neighbors(files, path, matching_key_only=True)
    if graph == "is_config_files":
        return _config_touchpoint_neighbors(files, path, matching_key_only=False)
    if graph == "sibling_directory":
        return _sibling_neighbors(files, path)
    if graph == "sibling_token_overlap":
        return _sibling_neighbors(
            files, path, query_tokens=query_tokens, tokenizer=tokenizer
        )
    if graph == "external_deps_overlap":
        return _external_dep_neighbors(files, path)
    return []


def build_slice_candidates(
    files: dict[str, dict[str, Any]],
    selected: list[RankedFile],
    profile: RetrievalProfileLike,
    *,
    clone_root: Path | None,
    slice_extractor: Any | None = None,
) -> list[SliceCandidate]:
    slice_candidates: list[SliceCandidate] = []
    for file_idx, candidate in enumerate(selected, start=1):
        entry = files.get(candidate.path, {})
        scored_entities = candidate.matched_entities or _default_scored_entities(entry)
        if not scored_entities:
            scored_entities = [ScoredEntity(entity=None, score=candidate.score)]
        reason = (
            "Matched search-plan hint"
            if candidate.exact_focus_match
            else "Adaptive graph expansion from seed match"
        )
        for entity_idx, scored_entity in enumerate(scored_entities):
            entity = scored_entity.entity
            start_line, end_line = _line_span(entity)
            if entity is None:
                start_line, end_line = _touchpoint_span(entry)
            (
                text,
                actual_start,
                actual_end,
                full_start,
                full_end,
                truncated_lines,
            ) = _slice_text(
                candidate.path,
                entry,
                entity,
                start_line,
                end_line,
                profile,
                clone_root=clone_root,
                slice_extractor=slice_extractor,
            )
            if text is None:
                continue
            symbol_path = entity.get("symbol_path") if entity else None
            label = (
                entity.get("name")
                if entity and entity.get("name")
                else Path(candidate.path).name
            )
            slice_candidates.append(
                SliceCandidate(
                    citation_id=f"code-{file_idx}-{entity_idx}",
                    file_path=candidate.path,
                    start_line=actual_start,
                    end_line=actual_end,
                    code=text,
                    symbol_path=symbol_path,
                    label=label,
                    score=scored_entity.score or candidate.score,
                    reason=reason,
                    full_start=full_start,
                    full_end=full_end,
                    truncated_lines=truncated_lines,
                )
            )
    return slice_candidates


def apply_token_budget(
    slice_candidates: list[SliceCandidate], token_budget: int
) -> list[SliceCandidate]:
    if token_budget <= 0:
        return []
    kept = list(slice_candidates)
    while (
        kept
        and sum(_approx_tokens(candidate.code) for candidate in kept) > token_budget
    ):
        lowest = min(
            range(len(kept)),
            key=lambda idx: (kept[idx].score, -idx),
        )
        kept.pop(lowest)
    return kept


def _graph_parts(graph: Any) -> tuple[str, str | None]:
    if isinstance(graph, tuple):
        primary, secondary = graph
        return str(primary), str(secondary) if secondary is not None else None
    primary = getattr(graph, "primary", None)
    if primary is not None:
        secondary = getattr(graph, "secondary", None)
        return str(primary), str(secondary) if secondary is not None else None
    return str(graph), None


def _focus_hint_score(
    entity: dict[str, Any],
    lower_path: str,
    focus_hints: list[str],
) -> int:
    score = 0
    symbol_path = str(entity.get("symbol_path", "")).lower()
    name = str(entity.get("name", "")).lower()
    for hint in focus_hints:
        if hint == symbol_path:
            score += 14
        elif hint == name:
            score += 10
        elif hint in symbol_path:
            score += 7
        elif hint in lower_path:
            score += 4
    return score


def _entity_tokens(
    entity: dict[str, Any], *, tokenizer: Tokenizer = _tokenize
) -> set[str]:
    tokens = set()
    tokens |= tokenizer(str(entity.get("name", "")))
    tokens |= tokenizer(str(entity.get("symbol_path", "")))
    tokens |= tokenizer(str(entity.get("signature", "")))
    tokens |= tokenizer(str(entity.get("docstring", "")))
    return tokens


def _is_low_signal_entry(
    entry: dict[str, Any],
    focus_hints: list[str],
    allow_config_files: bool,
    allow_test_files: bool = False,
) -> bool:
    path = str(entry.get("path", "")).lower()
    if any(hint == path or hint.replace(".", "/") in path for hint in focus_hints):
        return False
    if entry.get("is_test") and not allow_test_files:
        return True
    if entry.get("is_config") and not allow_config_files:
        return True
    return False


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _call_site_neighbors(files: dict[str, dict[str, Any]], path: str) -> list[str]:
    entry = files.get(path, {})
    seed_entity_names = {
        str(entity.get("name", "")).lower()
        for entity in entry.get("entities") or []
        if entity.get("name")
    }
    callees = {
        str(call_site.get("callee_name", "")).lower()
        for call_site in entry.get("call_sites") or []
        if call_site.get("callee_name")
    }
    neighbors: list[str] = []
    for candidate_path, candidate_entry in files.items():
        if candidate_path == path:
            continue
        candidate_names = {
            str(entity.get("name", "")).lower()
            for entity in candidate_entry.get("entities") or []
            if entity.get("name")
        }
        candidate_callees = {
            str(call_site.get("callee_name", "")).lower()
            for call_site in candidate_entry.get("call_sites") or []
            if call_site.get("callee_name")
        }
        if callees & candidate_names or seed_entity_names & candidate_callees:
            neighbors.append(candidate_path)
    return sorted(neighbors)


def _exception_touchpoint_neighbors(
    files: dict[str, dict[str, Any]],
    path: str,
    allow_test_files: bool,
    *,
    tokenizer: Tokenizer = _tokenize,
) -> list[str]:
    seed_touchpoints = files.get(path, {}).get("exception_touchpoints") or []
    seed_symbols = {
        str(touchpoint.get("symbol_path", "")).lower()
        for touchpoint in seed_touchpoints
        if touchpoint.get("symbol_path")
    }
    seed_message_tokens: set[str] = set()
    for touchpoint in seed_touchpoints:
        seed_message_tokens |= tokenizer(str(touchpoint.get("message", "")))
    if not seed_symbols and not seed_message_tokens:
        return []
    neighbors: list[str] = []
    for candidate_path, candidate_entry in files.items():
        if candidate_path == path:
            continue
        if candidate_entry.get("is_test") and not allow_test_files:
            continue
        for touchpoint in candidate_entry.get("exception_touchpoints") or []:
            symbol = str(touchpoint.get("symbol_path", "")).lower()
            message_tokens = tokenizer(str(touchpoint.get("message", "")))
            if symbol in seed_symbols or seed_message_tokens & message_tokens:
                neighbors.append(candidate_path)
                break
    return sorted(neighbors)


def _config_touchpoint_neighbors(
    files: dict[str, dict[str, Any]], path: str, *, matching_key_only: bool
) -> list[str]:
    seed_keys = _config_keys(files.get(path, {}))
    neighbors: list[str] = []
    for candidate_path, candidate_entry in files.items():
        if candidate_path == path:
            continue
        candidate_keys = _config_keys(candidate_entry)
        if seed_keys and candidate_keys & seed_keys:
            neighbors.append(candidate_path)
        elif not matching_key_only and candidate_entry.get("is_config"):
            neighbors.append(candidate_path)
    return sorted(neighbors)


def _config_keys(entry: dict[str, Any]) -> set[str]:
    return {
        str(touchpoint.get("config_key", "")).lower()
        for touchpoint in entry.get("config_touchpoints") or []
        if touchpoint.get("config_key")
    }


def _sibling_neighbors(
    files: dict[str, dict[str, Any]],
    path: str,
    *,
    query_tokens: set[str] | None = None,
    tokenizer: Tokenizer = _tokenize,
) -> list[str]:
    directory = str(Path(path).parent)
    if directory == ".":
        directory = ""
    neighbors: list[str] = []
    for candidate_path, candidate_entry in files.items():
        if candidate_path == path:
            continue
        candidate_directory = str(Path(candidate_path).parent)
        if candidate_directory == ".":
            candidate_directory = ""
        if candidate_directory != directory:
            continue
        if query_tokens is not None:
            candidate_tokens = set(candidate_entry.get("tokens") or []) | tokenizer(
                candidate_path
            )
            if not (query_tokens & candidate_tokens):
                continue
        neighbors.append(candidate_path)
    return sorted(neighbors)


def _external_dep_neighbors(files: dict[str, dict[str, Any]], path: str) -> list[str]:
    seed_deps = set(files.get(path, {}).get("external_deps") or [])
    if not seed_deps:
        return []
    return sorted(
        candidate_path
        for candidate_path, candidate_entry in files.items()
        if candidate_path != path
        and seed_deps & set(candidate_entry.get("external_deps") or [])
    )


def _default_scored_entities(entry: dict[str, Any]) -> list[ScoredEntity]:
    entity = _select_primary_entity(entry)
    if entity is None:
        return []
    return [ScoredEntity(entity=entity, score=0.1)]


def _select_primary_entity(entry: dict[str, Any]) -> dict[str, Any] | None:
    entities = entry.get("entities") or []
    if not entities:
        return None
    return sorted(
        entities,
        key=lambda entity: (
            entity.get("start_line") is None,
            int(entity.get("start_line") or 0),
            str(entity.get("name", "")),
        ),
    )[0]


def _line_span(entity: dict[str, Any] | None) -> tuple[int, int]:
    if entity is None:
        return 1, 1
    start_line = int(entity.get("start_line") or 1)
    end_line = int(entity.get("end_line") or start_line)
    return start_line, max(end_line, start_line)


def _touchpoint_span(entry: dict[str, Any]) -> tuple[int, int]:
    for key in ("config_touchpoints", "exception_touchpoints", "call_sites"):
        touchpoints = entry.get(key) or []
        if touchpoints:
            line = int(touchpoints[0].get("line") or 1)
            return line, line
    return 1, 1


def _slice_text(
    path: str,
    entry: dict[str, Any],
    entity: dict[str, Any] | None,
    start_line: int,
    end_line: int,
    profile: RetrievalProfileLike,
    *,
    clone_root: Path | None,
    slice_extractor: Any | None,
) -> tuple[str | None, int, int, int, int, int]:
    if clone_root is None:
        return (
            _build_snippet_text(path, entry, entity),
            start_line,
            end_line,
            max(1, start_line - 5),
            end_line + 5,
            0,
        )
    if slice_extractor is None:
        return None, start_line, end_line, start_line, end_line, 0
    result = slice_extractor(
        clone_root=clone_root,
        rel_path=path,
        anchor_start=start_line,
        anchor_end=end_line,
        line_cap=profile.line_cap,
        context_lines=5,
    )
    if result is None:
        return None, start_line, end_line, start_line, end_line, 0
    return (
        result.code,
        int(result.snippet_start),
        int(result.snippet_end),
        int(result.full_start),
        int(result.full_end),
        int(result.truncated_lines),
    )


def _apply_token_budget(
    slice_candidates: list[SliceCandidate], token_budget: int
) -> list[SliceCandidate]:
    return apply_token_budget(slice_candidates, token_budget)


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _build_snippet_text(
    path: str,
    entry: dict[str, Any],
    entity: dict[str, Any] | None,
) -> str:
    lines = [f"File: {path}"]
    if entity:
        entity_type = entity.get("type") or "symbol"
        name = entity.get("name") or Path(path).stem
        lines.append(f"{entity_type} {name}")
        signature = entity.get("signature")
        if signature:
            lines.append(f"signature: {signature}")
        docstring = entity.get("docstring")
        if docstring:
            lines.append(f"doc: {docstring}")
    if entry.get("imports"):
        lines.append(f"imports: {', '.join(entry['imports'][:3])}")
    if entry.get("imported_by"):
        lines.append(f"imported_by: {', '.join(entry['imported_by'][:3])}")
    if entry.get("external_deps"):
        lines.append(f"external_deps: {', '.join(entry['external_deps'][:3])}")
    return "\n".join(lines)
