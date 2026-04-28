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

_SEED_LIMIT = 2
_EXPANSION_DEPTH = 2
_RESULT_LIMIT = 4
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
    exact_focus_match: bool = False


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
):
    """Build deterministic code evidence from ``fast_report_index.json`` data."""

    from worker.fast_report import CodeEvidenceLayer

    files = index.get("files")
    if not isinstance(files, dict) or not files:
        return CodeEvidenceLayer()

    normalized_plan = _normalize_search_plan(plan)
    ranked = _rank_files(files, normalized_plan, question)
    seeds = [candidate for candidate in ranked if candidate.score > 0][:_SEED_LIMIT]
    selected = _expand_candidate_paths(files, seeds)

    snippets: list[dict[str, Any]] = []
    citations = []
    evidence_blocks = []
    for position, candidate in enumerate(selected, start=1):
        entry = files.get(candidate.path, {})
        entity = candidate.matched_entity or _select_primary_entity(entry)
        start_line, end_line = _line_span(entity)
        citation_id = f"code-{position}"
        reason = (
            "Matched search-plan hint"
            if candidate.exact_focus_match
            else "Dependency expansion from seed match"
        )
        text = _build_snippet_text(candidate.path, entry, entity)
        symbol_path = entity.get("symbol_path") if entity else None
        label = (
            entity.get("name")
            if entity and entity.get("name")
            else Path(candidate.path).name
        )
        snippets.append(
            {
                "file": candidate.path,
                "start_line": start_line,
                "end_line": end_line,
                "text": text,
                "score": candidate.score,
                "symbol_path": symbol_path,
            }
        )
        citations.append(
            _make_citation(
                citation_id=citation_id,
                file_path=candidate.path,
                start_line=start_line,
                end_line=end_line,
                label=label,
                score=candidate.score,
                reason=reason,
            )
        )
        evidence_blocks.append(
            _make_evidence_block(
                citation_id=citation_id,
                start_line=start_line,
                end_line=end_line,
                code=text,
                symbol_path=symbol_path,
            )
        )

    return CodeEvidenceLayer(
        snippets=snippets,
        citations=citations,
        evidence_blocks=evidence_blocks,
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
) -> list[_RankedFile]:
    query_tokens = _query_tokens(plan, question)
    focus_hints = [hint.lower() for hint in plan.retrieval_focus]
    allow_config_files = _is_config_relevant_query(plan, query_tokens)
    ranked: list[_RankedFile] = []
    for path, entry in files.items():
        if _is_low_signal_entry(entry, focus_hints, allow_config_files):
            continue
        candidate = _score_file(path, entry, query_tokens, focus_hints)
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
    lower_path = path.lower()
    file_tokens = set(entry.get("tokens") or []) | _tokenize(path)
    best_entity = _select_primary_entity(entry)
    entity_score = 0.0
    exact_focus_match = False

    score = float(len(query_tokens & file_tokens) * 2)
    if entry.get("imports"):
        score += 0.5
    if entry.get("imported_by"):
        score += 0.5

    matched_entity = None
    for hint in focus_hints:
        if hint == lower_path:
            score += 12
            exact_focus_match = True
        elif hint.replace(".", "/") in lower_path:
            score += 6

    for entity in entry.get("entities") or []:
        candidate_score = float(
            len(query_tokens & _entity_tokens(entity)) * 2
            + _focus_hint_score(entity, lower_path, focus_hints)
        )
        if candidate_score > entity_score:
            entity_score = candidate_score
            matched_entity = entity

    if matched_entity is not None:
        best_entity = matched_entity
        score += entity_score
        entity_symbol = str(matched_entity.get("symbol_path", "")).lower()
        exact_focus_match = exact_focus_match or any(
            hint == entity_symbol for hint in focus_hints
        )

    return _RankedFile(
        path=path,
        score=score,
        matched_entity=best_entity,
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
) -> bool:
    path = str(entry.get("path", "")).lower()
    if any(hint == path or hint.replace(".", "/") in path for hint in focus_hints):
        return False
    if entry.get("is_test"):
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
) -> list[_RankedFile]:
    if not seeds:
        return []

    ranked_lookup = {candidate.path: candidate for candidate in seeds}
    selected: list[_RankedFile] = []
    seen: set[str] = set()
    queue = deque((candidate, 0) for candidate in seeds)

    for candidate in seeds:
        selected.append(candidate)
        seen.add(candidate.path)

    while queue and len(selected) < _RESULT_LIMIT:
        candidate, depth = queue.popleft()
        if depth >= _EXPANSION_DEPTH:
            continue
        entry = files.get(candidate.path, {})
        neighbors = list(entry.get("imports") or []) + list(
            entry.get("imported_by") or []
        )
        for neighbor_path in neighbors:
            if neighbor_path in seen or neighbor_path not in files:
                continue
            neighbor_entry = files[neighbor_path]
            if _is_low_signal_entry(neighbor_entry, [], False):
                continue
            neighbor = _RankedFile(
                path=neighbor_path,
                score=max(candidate.score - (depth + 1), 0.1),
                matched_entity=_select_primary_entity(neighbor_entry),
                exact_focus_match=False,
            )
            ranked_lookup.setdefault(neighbor.path, neighbor)
            selected.append(ranked_lookup[neighbor.path])
            seen.add(neighbor_path)
            queue.append((ranked_lookup[neighbor.path], depth + 1))
            if len(selected) >= _RESULT_LIMIT:
                break

    return selected


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
):
    from shared.fast_report_types import FastReportEvidenceBlock

    return FastReportEvidenceBlock(
        citation_id=citation_id,
        snippet_start=start_line,
        snippet_end=end_line,
        full_start=max(1, start_line - 3),
        full_end=end_line + 3,
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
