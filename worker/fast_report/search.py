"""Helpers for fast-report planning and deterministic code retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from worker.fast_report.planning import QuestionTypeProfile
from worker.fast_report.planning import (
    expansion_graph_for as _planning_expansion_graph_for,
)
from worker.fast_report.planning import (
    profile_for_question_type as _planning_profile_for_question_type,
)
from worker.pipeline.retrieval.repo_search import (
    RankedFile,
    ScoredEntity,
    SliceCandidate,
    apply_token_budget,
    build_slice_candidates,
    expand_candidate_paths,
    score_file_for_query,
)
from worker.pipeline.retrieval.repo_search import (
    neighbors_for_graph as _repo_neighbors_for_graph,
)
from worker.utils.tokenize import tokenize_text as _tokenize

_CJK_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)

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
    """Adapter around :class:`QuestionTypeProfile` with retrieval-side names."""

    seed_limit: int
    depth: int
    result_limit: int
    token_budget: int
    line_cap: int
    slices_per_file: int


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


_RankedFile = RankedFile
_ScoredEntity = ScoredEntity
_SliceCandidate = SliceCandidate
_score_file_multi_slice = score_file_for_query
_build_slice_candidates = build_slice_candidates
_apply_token_budget = apply_token_budget
_neighbors_for_graph = _repo_neighbors_for_graph


def _to_retrieval_profile(profile: QuestionTypeProfile) -> _RetrievalProfile:
    return _RetrievalProfile(
        seed_limit=profile.seed,
        depth=profile.depth,
        result_limit=profile.result_limit,
        token_budget=profile.code_evidence_token_budget,
        line_cap=profile.per_slice_line_cap,
        slices_per_file=profile.slices_per_file,
    )


def profile_for_question_type(question_type: str | None) -> _RetrievalProfile:
    return _to_retrieval_profile(
        _planning_profile_for_question_type((question_type or "").strip().lower())
    )


def expansion_graph_for(question_type: str | None) -> tuple[str, str | None]:
    graph = _planning_expansion_graph_for((question_type or "").strip().lower())
    return graph.primary, graph.secondary


def detect_question_language(question: str) -> str:
    """Detect the question language for fast reports."""

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
    query_tokens = _query_tokens(normalized_plan, question)
    ranked = _rank_files(files, normalized_plan, query_tokens, profile)
    seeds = [candidate for candidate in ranked if candidate.score > 0][
        : profile.seed_limit
    ]
    selected = _expand_candidate_paths(
        files,
        seeds,
        ranked,
        normalized_plan,
        query_tokens,
        profile,
    )
    slice_candidates = build_slice_candidates(
        files,
        selected,
        profile,
        clone_root=clone_root,
        slice_extractor=_load_slice_extractor() if clone_root is not None else None,
    )
    slice_count_before_budget = len(slice_candidates)
    slice_candidates = apply_token_budget(slice_candidates, effective_budget)
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
    query_tokens: set[str],
    profile: _RetrievalProfile,
) -> list[RankedFile]:
    focus_hints = [hint.lower() for hint in plan.retrieval_focus]
    allow_config_files = _is_config_relevant_query(plan, query_tokens)
    allow_test_files = plan.question_type.lower() == "testing"
    ranked: list[RankedFile] = []
    for path, entry in files.items():
        if _is_low_signal_entry(
            entry, focus_hints, allow_config_files, allow_test_files
        ):
            continue
        candidate = score_file_for_query(
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


def _expand_candidate_paths(
    files: dict[str, dict[str, Any]],
    seeds: list[RankedFile],
    ranked: list[RankedFile] | None = None,
    plan: _NormalizedSearchPlan | None = None,
    question_or_tokens: str | set[str] = "",
    profile: _RetrievalProfile | None = None,
) -> list[RankedFile]:
    profile = profile or profile_for_question_type(None)
    query_tokens = (
        question_or_tokens
        if isinstance(question_or_tokens, set)
        else (
            _query_tokens(plan, question_or_tokens)
            if plan
            else _tokenize(question_or_tokens)
        )
    )
    question_type = plan.question_type if plan else None
    allow_config_files = (
        _is_config_relevant_query(plan, query_tokens) if plan else False
    )
    allow_test_files = bool(plan and plan.question_type.lower() == "testing")
    return expand_candidate_paths(
        files,
        seeds,
        ranked,
        graph=expansion_graph_for(question_type),
        query_tokens=query_tokens,
        profile=profile,
        allow_config_files=allow_config_files,
        allow_test_files=allow_test_files,
    )


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


def _load_slice_extractor():
    try:
        from worker.fast_report.slices import extract_source_slice
    except ImportError:
        return None
    return extract_source_slice


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
