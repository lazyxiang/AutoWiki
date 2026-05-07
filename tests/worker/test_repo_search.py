from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass

import pytest


@dataclass(frozen=True, slots=True)
class _Profile:
    seed_limit: int = 2
    depth: int = 1
    result_limit: int = 4
    token_budget: int = 500
    line_cap: int = 50
    slices_per_file: int = 2


def test_repo_search_exports_domain_agnostic_primitives():
    repo_search = importlib.import_module("worker.pipeline.retrieval.repo_search")

    for symbol in (
        "score_file_for_query",
        "expand_candidate_paths",
        "build_slice_candidates",
        "apply_token_budget",
        "neighbors_for_graph",
        "RankedFile",
        "ScoredEntity",
        "SliceCandidate",
        "register_expansion_profile",
        "expansion_graph_for",
    ):
        assert hasattr(repo_search, symbol)


def test_importing_repo_search_does_not_import_fast_report_modules(monkeypatch):
    for module_name in (
        "worker.pipeline.retrieval.repo_search",
        "worker.fast_report.search",
        "worker.fast_report.planning",
    ):
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    importlib.import_module("worker.pipeline.retrieval.repo_search")

    assert "worker.fast_report.search" not in sys.modules
    assert "worker.fast_report.planning" not in sys.modules


def test_expansion_profile_registry_returns_registered_config_and_raises_key_error():
    from worker.pipeline.retrieval.repo_search import (
        expansion_graph_for,
        register_expansion_profile,
    )

    config = ("imports", "sibling_directory")

    register_expansion_profile("test-profile", config)

    assert expansion_graph_for("test-profile") == config
    with pytest.raises(KeyError, match="known profiles"):
        expansion_graph_for("missing-profile")


def test_score_expand_slice_and_budget_primitives_are_domain_agnostic():
    from worker.pipeline.retrieval.repo_search import (
        apply_token_budget,
        build_slice_candidates,
        expand_candidate_paths,
        score_file_for_query,
    )

    profile = _Profile()
    files = {
        "app/service.py": {
            "path": "app/service.py",
            "tokens": ["service", "alpha"],
            "imports": ["app/model.py"],
            "imported_by": [],
            "external_deps": [],
            "entities": [
                {
                    "name": "build_alpha",
                    "type": "function",
                    "start_line": 10,
                    "end_line": 20,
                    "signature": "def build_alpha()",
                    "docstring": "Build alpha service.",
                    "symbol_path": "app.service.build_alpha",
                },
                {
                    "name": "serve_alpha",
                    "type": "function",
                    "start_line": 30,
                    "end_line": 40,
                    "signature": "def serve_alpha()",
                    "docstring": "Serve alpha requests.",
                    "symbol_path": "app.service.serve_alpha",
                },
            ],
            "is_test": False,
            "is_config": False,
        },
        "app/model.py": {
            "path": "app/model.py",
            "tokens": ["model", "alpha"],
            "imports": [],
            "imported_by": ["app/service.py"],
            "external_deps": [],
            "entities": [
                {
                    "name": "AlphaModel",
                    "type": "class",
                    "start_line": 1,
                    "end_line": 5,
                    "symbol_path": "app.model.AlphaModel",
                }
            ],
            "is_test": False,
            "is_config": False,
        },
    }

    ranked = score_file_for_query(
        "app/service.py",
        files["app/service.py"],
        {"alpha", "service"},
        [],
        profile,
    )
    selected = expand_candidate_paths(
        files,
        [ranked],
        ranked=[ranked],
        graph=("imports", None),
        query_tokens={"alpha"},
        profile=profile,
    )
    slices = build_slice_candidates(files, selected, profile, clone_root=None)
    kept = apply_token_budget(slices, 10_000)

    assert [candidate.path for candidate in selected] == [
        "app/service.py",
        "app/model.py",
    ]
    assert [candidate.symbol_path for candidate in kept[:2]] == [
        "app.service.build_alpha",
        "app.service.serve_alpha",
    ]
    assert kept[0].citation_id == "code-1-0"


def test_score_file_for_query_accepts_custom_tokenizer_for_short_tokens():
    from worker.pipeline.retrieval.repo_search import score_file_for_query
    from worker.utils.tokenize import tokenize_text

    profile = _Profile()
    entry = {
        "path": "app/ui.py",
        "tokens": [],
        "imports": [],
        "imported_by": [],
        "entities": [
            {
                "name": "UI",
                "type": "class",
                "start_line": 1,
                "end_line": 5,
                "signature": "class UI",
                "symbol_path": "app.ui.UI",
            }
        ],
        "is_test": False,
        "is_config": False,
    }

    ranked = score_file_for_query(
        "app/ui.py",
        entry,
        {"ui"},
        [],
        profile,
        tokenizer=lambda text: tokenize_text(text, min_ascii_len=2),
    )

    assert ranked.score > 0
    assert ranked.matched_entity == entry["entities"][0]


def test_score_file_for_query_default_tokenizer_scores_two_character_signals():
    from worker.pipeline.retrieval.repo_search import score_file_for_query

    profile = _Profile()
    entry = {
        "path": "app/ui.py",
        "tokens": [],
        "imports": [],
        "imported_by": [],
        "entities": [
            {
                "name": "DB",
                "type": "class",
                "start_line": 1,
                "end_line": 5,
                "signature": "class DB",
                "symbol_path": "app.ui.DB",
            }
        ],
        "is_test": False,
        "is_config": False,
    }

    ranked = score_file_for_query(
        "app/ui.py",
        entry,
        {"ui", "db"},
        [],
        profile,
    )

    assert ranked.score > 0
    assert ranked.matched_entity == entry["entities"][0]


def test_fast_report_search_compatibility_exports_retrieval_and_tuple_graph():
    from worker.fast_report.search import expansion_graph_for, retrieve_code_evidence

    assert callable(retrieve_code_evidence)
    assert expansion_graph_for("architecture") == (
        "imports_and_imported_by",
        "sibling_directory",
    )
