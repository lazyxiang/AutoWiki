"""Helpers for fast-report planning and deterministic code retrieval."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

_CJK_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)
_CJK_RUN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+"
)
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_CASE_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_CONFIG_QUERY_TOKENS = {
    "config",
    "configuration",
    "configure",
    "configured",
    "env",
    "environment",
    "settings",
    "provider",
}


@dataclass(frozen=True, slots=True)
class _RetrievalProfile:
    seed_limit: int
    depth: int
    result_limit: int
    token_budget: int
    line_cap: int
    slices_per_file: int


_DEFAULT_PROFILE = _RetrievalProfile(
    seed_limit=2,
    depth=2,
    result_limit=6,
    token_budget=40_000,
    line_cap=50,
    slices_per_file=1,
)
_RETRIEVAL_PROFILES = {
    "architecture": _RetrievalProfile(4, 3, 12, 50_000, 40, 3),
    "execution_flow": _RetrievalProfile(3, 3, 10, 50_000, 50, 2),
    "dependency": _RetrievalProfile(3, 2, 10, 40_000, 30, 1),
    "error_handling": _RetrievalProfile(2, 2, 8, 35_000, 40, 2),
    "configuration": _RetrievalProfile(3, 2, 8, 35_000, 30, 2),
    "testing": _RetrievalProfile(2, 1, 6, 40_000, 60, 2),
    "implementation_location": _RetrievalProfile(2, 1, 4, 25_000, 200, 1),
    "unknown": _DEFAULT_PROFILE,
}

_EXPANSION_GRAPHS: dict[str, tuple[str, str | None]] = {
    "architecture": ("imports_and_imported_by", "sibling_directory"),
    "execution_flow": ("call_sites", "imports"),
    "error_handling": ("exception_touchpoints", "imports"),
    "configuration": ("config_touchpoints", "is_config_files"),
    "dependency": ("imports_and_imported_by", "external_deps_overlap"),
    "testing": ("sibling_token_overlap", "imports"),
    "implementation_location": ("imports", None),
    "unknown": ("imports_and_imported_by", None),
}


class _FastReportPlanLike(Protocol):
    question_type: str
    target: str
    answer_shape: str
    evidence_shape: str
    search_terms: list[str]
    retrieval_focus: list[str]


@dataclass(slots=True)
class _NormalizedSearchPlan:
    question_type: str = ""
    target: str = ""
    answer_shape: str = ""
    evidence_shape: str = ""
    search_terms: list[str] | None = None
    retrieval_focus: list[str] | None = None

    def __post_init__(self) -> None:
        self.search_terms = list(self.search_terms or [])
        self.retrieval_focus = list(self.retrieval_focus or [])


@dataclass(slots=True)
class _RankedFile:
    path: str
    score: float
    matched_entity: dict[str, Any] | None
    matched_entities: list[_ScoredEntity] | None = None
    exact_focus_match: bool = False


@dataclass(slots=True)
class _ScoredEntity:
    entity: dict[str, Any] | None
    score: float


@dataclass(slots=True)
class _SliceCandidate:
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


def profile_for_question_type(question_type: str | None) -> _RetrievalProfile:
    return _RETRIEVAL_PROFILES.get(
        (question_type or "").strip().lower(), _DEFAULT_PROFILE
    )


def expansion_graph_for(question_type: str | None) -> tuple[str, str | None]:
    return _EXPANSION_GRAPHS.get(
        (question_type or "").strip().lower(), _EXPANSION_GRAPHS["unknown"]
    )


def detect_question_language(question: str) -> str:
    """Detect the question language for fast reports.

    The detector maps CJK-script questions onto the Chinese prompt/rendering
    path because fast reports currently only have ``en`` and ``zh`` language
    instructions.
    """

    if _CJK_RE.search(question):
        return "zh"
    return "en"


def normalize_fast_report_language(language: str | None) -> str:
    """Normalize planner or detector language labels to supported values."""

    value = (language or "").strip().lower().replace("_", "-")
    if not value:
        return "en"
    if (
        value.startswith("zh")
        or value.startswith("ja")
        or value.startswith("ko")
        or "chinese" in value
        or "japanese" in value
        or "korean" in value
    ):
        return "zh"
    if value.startswith("en") or "english" in value:
        return "en"
    return "en"


def retrieve_code_evidence(
    index: dict[str, Any],
    plan: _FastReportPlanLike | dict[str, Any],
    question: str,
    *,
    clone_root: Path | None = None,
    token_budget: int | None = None,
):
    """Build deterministic code evidence from ``fast_report_index.json`` data."""

    from worker.fast_report import CodeEvidenceLayer

    files = index.get("files")
    if not isinstance(files, dict) or not files:
        return CodeEvidenceLayer()

    normalized_plan = _normalize_search_plan(plan)
    profile = profile_for_question_type(normalized_plan.question_type)
    effective_budget = (
        token_budget if token_budget is not None else profile.token_budget
    )
    ranked = _rank_files(files, normalized_plan, question, profile)
    seeds = [candidate for candidate in ranked if candidate.score > 0][
        : profile.seed_limit
    ]
    selected = _expand_candidate_paths(
        files, seeds, ranked, normalized_plan, question, profile
    )
    slice_candidates = _build_slice_candidates(
        files,
        selected,
        profile,
        clone_root=clone_root,
    )
    slice_count_before_budget = len(slice_candidates)
    slice_candidates = _apply_token_budget(slice_candidates, effective_budget)
    dropped_due_to_budget = slice_count_before_budget - len(slice_candidates)

    snippets: list[dict[str, Any]] = []
    citations = []
    evidence_blocks = []
    for candidate in slice_candidates:
        snippets.append(
            {
                "file": candidate.file_path,
                "start_line": candidate.start_line,
                "end_line": candidate.end_line,
                "text": candidate.code,
                "score": candidate.score,
                "symbol_path": candidate.symbol_path,
            }
        )
        citations.append(
            _make_citation(
                citation_id=candidate.citation_id,
                file_path=candidate.file_path,
                start_line=candidate.start_line,
                end_line=candidate.end_line,
                label=candidate.label,
                score=candidate.score,
                reason=candidate.reason,
            )
        )
        evidence_blocks.append(
            _make_evidence_block(
                citation_id=candidate.citation_id,
                start_line=candidate.start_line,
                end_line=candidate.end_line,
                code=candidate.code,
                symbol_path=candidate.symbol_path,
                full_start=candidate.full_start,
                full_end=candidate.full_end,
            )
        )

    return CodeEvidenceLayer(
        snippets=snippets,
        citations=citations,
        evidence_blocks=evidence_blocks,
        retrieval_metadata={"dropped_due_to_budget": dropped_due_to_budget},
    )


def _normalize_search_plan(
    plan: _FastReportPlanLike | dict[str, Any],
) -> _NormalizedSearchPlan:
    if isinstance(plan, dict):
        getter = plan.get
    else:

        def getter(name: str, default=None):
            return getattr(plan, name, default)

    return _NormalizedSearchPlan(
        question_type=str(getter("question_type", "") or ""),
        target=str(getter("target", "") or ""),
        answer_shape=str(getter("answer_shape", "") or ""),
        evidence_shape=str(getter("evidence_shape", "") or ""),
        search_terms=normalize_string_list(getter("search_terms", []) or []),
        retrieval_focus=normalize_string_list(getter("retrieval_focus", []) or []),
    )


def normalize_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized


def _rank_files(
    files: dict[str, dict[str, Any]],
    plan: _NormalizedSearchPlan,
    question: str,
    profile: _RetrievalProfile,
) -> list[_RankedFile]:
    query_tokens = _query_tokens(plan, question)
    focus_hints = [hint.lower() for hint in plan.retrieval_focus]
    allow_config_files = _is_config_relevant_query(plan, query_tokens)
    allow_test_files = plan.question_type.lower() == "testing"
    ranked: list[_RankedFile] = []
    for path, entry in files.items():
        if _is_low_signal_entry(
            entry, focus_hints, allow_config_files, allow_test_files
        ):
            continue
        candidate = _score_file_multi_slice(
            path, entry, query_tokens, focus_hints, profile
        )
        if candidate.score <= 0:
            continue
        ranked.append(candidate)
    return sorted(
        ranked,
        key=lambda candidate: (
            -candidate.score,
            not candidate.exact_focus_match,
            candidate.path,
        ),
    )


def _query_tokens(plan: _NormalizedSearchPlan, question: str) -> set[str]:
    tokens = _tokenize(question)
    tokens |= _tokenize(plan.question_type.replace("_", " "))
    tokens |= _tokenize(plan.target)
    tokens |= _tokenize(plan.answer_shape)
    tokens |= _tokenize(plan.evidence_shape)
    for item in plan.search_terms + plan.retrieval_focus:
        tokens |= _tokenize(item)
    return tokens


def _score_file(
    path: str,
    entry: dict[str, Any],
    query_tokens: set[str],
    focus_hints: list[str],
) -> _RankedFile:
    return _score_file_multi_slice(
        path, entry, query_tokens, focus_hints, _DEFAULT_PROFILE
    )


def _score_file_multi_slice(
    path: str,
    entry: dict[str, Any],
    query_tokens: set[str],
    focus_hints: list[str],
    profile: _RetrievalProfile,
) -> _RankedFile:
    lower_path = path.lower()
    file_tokens = set(entry.get("tokens") or []) | _tokenize(path)
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

    scored_entities: list[_ScoredEntity] = []
    for entity in entry.get("entities") or []:
        candidate_score = float(
            len(query_tokens & _entity_tokens(entity)) * 2
            + _focus_hint_score(entity, lower_path, focus_hints)
        )
        scored_entities.append(_ScoredEntity(entity=entity, score=candidate_score))

    scored_entities.sort(
        key=lambda item: (
            -item.score,
            int(item.entity.get("start_line") or 0) if item.entity else 0,
            str(item.entity.get("name", "")) if item.entity else "",
        )
    )
    selected_entities: list[_ScoredEntity] = []
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

    return _RankedFile(
        path=path,
        score=score,
        matched_entity=best_entity,
        matched_entities=selected_entities,
        exact_focus_match=exact_focus_match,
    )


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


def _entity_tokens(entity: dict[str, Any]) -> set[str]:
    tokens = set()
    tokens |= _tokenize(str(entity.get("name", "")))
    tokens |= _tokenize(str(entity.get("symbol_path", "")))
    tokens |= _tokenize(str(entity.get("signature", "")))
    tokens |= _tokenize(str(entity.get("docstring", "")))
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


def _is_config_relevant_query(
    plan: _NormalizedSearchPlan, query_tokens: set[str]
) -> bool:
    if plan.question_type.lower() == "configuration":
        return True
    if "config" in plan.evidence_shape.lower():
        return True
    return len(query_tokens & _CONFIG_QUERY_TOKENS) >= 2


def _expand_candidate_paths(
    files: dict[str, dict[str, Any]],
    seeds: list[_RankedFile],
    ranked: list[_RankedFile] | None = None,
    plan: _NormalizedSearchPlan | None = None,
    question: str = "",
    profile: _RetrievalProfile = _DEFAULT_PROFILE,
) -> list[_RankedFile]:
    if not seeds:
        return []

    ranked_lookup = {candidate.path: candidate for candidate in (ranked or seeds)}
    query_tokens = (
        _query_tokens(plan, question) if plan is not None else _tokenize(question)
    )
    primary_graph, fallback_graph = expansion_graph_for(
        plan.question_type if plan else None
    )
    allow_config_files = (
        _is_config_relevant_query(plan, query_tokens) if plan else False
    )
    allow_test_files = bool(plan and plan.question_type.lower() == "testing")
    selected: list[_RankedFile] = []
    seen: set[str] = set()
    queue = deque((candidate, 0) for candidate in seeds)

    for candidate in seeds:
        selected.append(candidate)
        seen.add(candidate.path)

    while queue and len(selected) < profile.result_limit:
        candidate, depth = queue.popleft()
        if depth >= profile.depth:
            continue
        neighbors = _neighbors_for_graph(
            files,
            candidate.path,
            primary_graph,
            query_tokens,
            allow_test_files=allow_test_files,
        )
        if not neighbors and fallback_graph:
            neighbors = _neighbors_for_graph(
                files,
                candidate.path,
                fallback_graph,
                query_tokens,
                allow_test_files=allow_test_files,
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
                neighbor = _RankedFile(
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


def _neighbors_for_graph(
    files: dict[str, dict[str, Any]],
    path: str,
    graph: str,
    query_tokens: set[str],
    *,
    allow_test_files: bool,
) -> list[str]:
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
        return _exception_touchpoint_neighbors(files, path, allow_test_files)
    if graph == "config_touchpoints":
        return _config_touchpoint_neighbors(files, path, matching_key_only=True)
    if graph == "is_config_files":
        return _config_touchpoint_neighbors(files, path, matching_key_only=False)
    if graph == "sibling_directory":
        return _sibling_neighbors(files, path)
    if graph == "sibling_token_overlap":
        return _sibling_neighbors(files, path, query_tokens=query_tokens)
    if graph == "external_deps_overlap":
        return _external_dep_neighbors(files, path)
    return []


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
    files: dict[str, dict[str, Any]], path: str, allow_test_files: bool
) -> list[str]:
    seed_touchpoints = files.get(path, {}).get("exception_touchpoints") or []
    seed_symbols = {
        str(touchpoint.get("symbol_path", "")).lower()
        for touchpoint in seed_touchpoints
        if touchpoint.get("symbol_path")
    }
    seed_message_tokens: set[str] = set()
    for touchpoint in seed_touchpoints:
        seed_message_tokens |= _tokenize(str(touchpoint.get("message", "")))
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
            message_tokens = _tokenize(str(touchpoint.get("message", "")))
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
            candidate_tokens = set(candidate_entry.get("tokens") or []) | _tokenize(
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


def _default_scored_entities(entry: dict[str, Any]) -> list[_ScoredEntity]:
    entity = _select_primary_entity(entry)
    if entity is None:
        return []
    return [_ScoredEntity(entity=entity, score=0.1)]


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


def _build_slice_candidates(
    files: dict[str, dict[str, Any]],
    selected: list[_RankedFile],
    profile: _RetrievalProfile,
    *,
    clone_root: Path | None,
) -> list[_SliceCandidate]:
    slice_candidates: list[_SliceCandidate] = []
    for file_idx, candidate in enumerate(selected, start=1):
        entry = files.get(candidate.path, {})
        scored_entities = candidate.matched_entities or _default_scored_entities(entry)
        if not scored_entities:
            scored_entities = [_ScoredEntity(entity=None, score=candidate.score)]
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
                _SliceCandidate(
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
    profile: _RetrievalProfile,
    *,
    clone_root: Path | None,
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
    extractor = _load_slice_extractor()
    if extractor is None:
        return None, start_line, end_line, start_line, end_line, 0
    result = extractor(
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


def _load_slice_extractor():
    try:
        from worker.fast_report_slices import extract_source_slice
    except ImportError:
        return None
    return extract_source_slice


def _apply_token_budget(
    slice_candidates: list[_SliceCandidate], token_budget: int
) -> list[_SliceCandidate]:
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


def _make_citation(
    *,
    citation_id: str,
    file_path: str,
    start_line: int,
    end_line: int,
    label: str,
    score: float,
    reason: str,
):
    from shared.fast_report_types import FastReportCitation

    return FastReportCitation(
        id=citation_id,
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        label=label,
        kind="code_evidence",
        reason=reason,
        score=score,
    )


def _make_evidence_block(
    *,
    citation_id: str,
    start_line: int,
    end_line: int,
    code: str,
    symbol_path: str | None,
    full_start: int | None = None,
    full_end: int | None = None,
):
    from shared.fast_report_types import FastReportEvidenceBlock

    return FastReportEvidenceBlock(
        citation_id=citation_id,
        snippet_start=start_line,
        snippet_end=end_line,
        full_start=full_start if full_start is not None else max(1, start_line - 5),
        full_end=full_end if full_end is not None else end_line + 5,
        code=code,
        symbol_path=symbol_path,
    )


def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    # CJK runs: add whole run plus bigrams/trigrams so partial matches work.
    for run in _CJK_RUN_RE.findall(text.lower()):
        tokens.add(run)
        max_ngram = min(3, len(run))
        for size in range(2, max_ngram + 1):
            for i in range(len(run) - size + 1):
                tokens.add(run[i : i + size])
    for part in _TOKEN_SPLIT_RE.split(text.replace("/", " ").replace(".", " ")):
        if not part:
            continue
        for camel_part in _CAMEL_CASE_RE.split(part):
            normalized = camel_part.strip().lower()
            if len(normalized) >= 2:
                tokens.add(normalized)
    return tokens
