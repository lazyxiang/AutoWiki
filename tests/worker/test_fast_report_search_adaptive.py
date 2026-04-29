from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class _Plan:
    question_type: str
    target: str = ""
    answer_shape: str = ""
    evidence_shape: str = ""
    search_terms: list[str] | None = None
    retrieval_focus: list[str] | None = None


@dataclass(frozen=True, slots=True)
class _FakeSliceResult:
    snippet_start: int
    snippet_end: int
    full_start: int
    full_end: int
    code: str
    truncated_lines: int


def _install_fake_slice_extractor(monkeypatch, *, fail_paths: set[str] | None = None):
    fail_paths = fail_paths or set()
    module = types.ModuleType("worker.fast_report_slices")

    def extract_source_slice(
        *,
        clone_root: Path,
        rel_path: str,
        anchor_start: int,
        anchor_end: int,
        line_cap: int,
        context_lines: int = 5,
    ):
        if rel_path in fail_paths:
            return None
        source_path = clone_root / rel_path
        lines = source_path.read_text().splitlines()
        snippet_end = min(anchor_end, anchor_start + line_cap - 1, len(lines))
        full_start = max(1, anchor_start - context_lines)
        full_end = min(len(lines), snippet_end + context_lines)
        code = "\n".join(lines[anchor_start - 1 : snippet_end])
        return _FakeSliceResult(
            snippet_start=anchor_start,
            snippet_end=snippet_end,
            full_start=full_start,
            full_end=full_end,
            code=code,
            truncated_lines=max(0, anchor_end - snippet_end),
        )

    module.extract_source_slice = extract_source_slice
    monkeypatch.setitem(sys.modules, "worker.fast_report_slices", module)


def test_profile_for_question_type_uses_adaptive_budgets():
    from worker.fast_report_search import profile_for_question_type

    assert profile_for_question_type("architecture").seed_limit == 4
    assert profile_for_question_type("architecture").slices_per_file == 3
    assert profile_for_question_type("execution_flow").result_limit == 10
    assert profile_for_question_type("dependency").token_budget == 40_000
    assert profile_for_question_type("unknown").line_cap == 50
    assert profile_for_question_type("not-a-type").result_limit == 6


def test_expansion_graph_for_maps_question_types():
    from worker.fast_report_search import expansion_graph_for

    assert expansion_graph_for("architecture") == (
        "imports_and_imported_by",
        "sibling_directory",
    )
    assert expansion_graph_for("execution_flow") == ("call_sites", "imports")
    assert expansion_graph_for("error_handling") == (
        "exception_touchpoints",
        "imports",
    )
    assert expansion_graph_for("configuration") == (
        "config_touchpoints",
        "is_config_files",
    )
    assert expansion_graph_for("dependency") == (
        "imports_and_imported_by",
        "external_deps_overlap",
    )
    assert expansion_graph_for("testing") == ("sibling_token_overlap", "imports")
    assert expansion_graph_for("implementation_location") == ("imports", None)
    assert expansion_graph_for("other") == ("imports_and_imported_by", None)


def test_architecture_retrieval_emits_top_k_multi_slice_source_citations(
    tmp_path, monkeypatch
):
    from worker.fast_report_search import retrieve_code_evidence

    _install_fake_slice_extractor(monkeypatch)
    clone_root = tmp_path
    source_dir = clone_root / "app"
    source_dir.mkdir()
    (source_dir / "service.py").write_text(
        "\n".join(f"line {line}" for line in range(1, 101))
    )
    index = {
        "files": {
            "app/service.py": {
                "path": "app/service.py",
                "tokens": ["app", "service"],
                "imports": [],
                "imported_by": [],
                "external_deps": [],
                "entities": [
                    {
                        "name": "alpha_handler",
                        "type": "function",
                        "start_line": 10,
                        "end_line": 20,
                        "signature": "def alpha_handler()",
                        "docstring": "Alpha workflow handler.",
                        "symbol_path": "app.service.alpha_handler",
                    },
                    {
                        "name": "beta_handler",
                        "type": "function",
                        "start_line": 30,
                        "end_line": 40,
                        "signature": "def beta_handler()",
                        "docstring": "Beta workflow handler.",
                        "symbol_path": "app.service.beta_handler",
                    },
                    {
                        "name": "gamma_handler",
                        "type": "function",
                        "start_line": 50,
                        "end_line": 60,
                        "signature": "def gamma_handler()",
                        "docstring": "Gamma workflow handler.",
                        "symbol_path": "app.service.gamma_handler",
                    },
                    {
                        "name": "unrelated",
                        "type": "function",
                        "start_line": 80,
                        "end_line": 90,
                        "signature": "def unrelated()",
                        "docstring": "No matching topic.",
                        "symbol_path": "app.service.unrelated",
                    },
                ],
                "is_test": False,
                "is_config": False,
            }
        }
    }
    plan = _Plan(
        question_type="architecture",
        target="service handlers",
        search_terms=["alpha", "beta", "gamma"],
        retrieval_focus=[],
    )

    layer = retrieve_code_evidence(
        index,
        plan,
        "How are alpha beta gamma service handlers organized?",
        clone_root=clone_root,
    )

    assert [citation.id for citation in layer.citations] == [
        "code-1-0",
        "code-1-1",
        "code-1-2",
    ]
    assert [block.symbol_path for block in layer.evidence_blocks] == [
        "app.service.alpha_handler",
        "app.service.beta_handler",
        "app.service.gamma_handler",
    ]
    assert [block.full_start for block in layer.evidence_blocks] == [5, 25, 45]
    assert "File: app/service.py" not in layer.evidence_blocks[0].code


def test_execution_flow_expands_call_sites_without_imported_by(tmp_path, monkeypatch):
    from worker.fast_report_search import retrieve_code_evidence

    _install_fake_slice_extractor(monkeypatch)
    (tmp_path / "app").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "app" / "entry.py").write_text("entry\n" * 20)
    (tmp_path / "app" / "flow.py").write_text("flow\n" * 20)
    (tmp_path / "tests" / "test_entry.py").write_text("test\n" * 20)
    index = {
        "files": {
            "app/entry.py": {
                "path": "app/entry.py",
                "tokens": ["entry", "pipeline"],
                "imports": [],
                "imported_by": ["tests/test_entry.py"],
                "external_deps": [],
                "call_sites": [{"callee_name": "run_pipeline", "line": 3}],
                "entities": [
                    {
                        "name": "start_pipeline",
                        "start_line": 1,
                        "end_line": 5,
                        "symbol_path": "app.entry.start_pipeline",
                    }
                ],
                "is_test": False,
                "is_config": False,
            },
            "app/flow.py": {
                "path": "app/flow.py",
                "tokens": ["flow", "pipeline"],
                "imports": [],
                "imported_by": [],
                "external_deps": [],
                "call_sites": [],
                "entities": [
                    {
                        "name": "run_pipeline",
                        "start_line": 2,
                        "end_line": 8,
                        "symbol_path": "app.flow.run_pipeline",
                    }
                ],
                "is_test": False,
                "is_config": False,
            },
            "tests/test_entry.py": {
                "path": "tests/test_entry.py",
                "tokens": ["entry", "pipeline"],
                "imports": ["app/entry.py"],
                "imported_by": [],
                "external_deps": [],
                "call_sites": [],
                "entities": [],
                "is_test": True,
                "is_config": False,
            },
        }
    }
    plan = _Plan(
        question_type="execution_flow",
        search_terms=["pipeline"],
        retrieval_focus=["app.entry.start_pipeline"],
    )

    layer = retrieve_code_evidence(
        index,
        plan,
        "How does start_pipeline run the pipeline?",
        clone_root=tmp_path,
    )

    assert [citation.file_path for citation in layer.citations] == [
        "app/entry.py",
        "app/flow.py",
    ]


def test_configuration_expands_matching_config_touchpoints(tmp_path, monkeypatch):
    from worker.fast_report_search import retrieve_code_evidence

    _install_fake_slice_extractor(monkeypatch)
    (tmp_path / "app").mkdir()
    (tmp_path / "misc").mkdir()
    (tmp_path / "app" / "settings.py").write_text("settings\n" * 20)
    (tmp_path / "app" / "client.py").write_text("client\n" * 20)
    (tmp_path / "misc" / "timeout.py").write_text("other\n" * 20)
    index = {
        "files": {
            "app/client.py": {
                "path": "app/client.py",
                "tokens": ["client", "api", "key"],
                "imports": [],
                "imported_by": [],
                "external_deps": [],
                "config_touchpoints": [
                    {"kind": "read", "config_key": "API_KEY", "line": 4}
                ],
                "entities": [
                    {
                        "name": "build_client",
                        "start_line": 2,
                        "end_line": 8,
                        "symbol_path": "app.client.build_client",
                    }
                ],
                "is_test": False,
                "is_config": False,
            },
            "app/settings.py": {
                "path": "app/settings.py",
                "tokens": ["settings", "api", "key"],
                "imports": [],
                "imported_by": [],
                "external_deps": [],
                "config_touchpoints": [
                    {"kind": "read", "config_key": "API_KEY", "line": 6}
                ],
                "entities": [],
                "is_test": False,
                "is_config": True,
            },
            "misc/timeout.py": {
                "path": "misc/timeout.py",
                "tokens": ["timeout"],
                "imports": [],
                "imported_by": [],
                "external_deps": [],
                "config_touchpoints": [
                    {"kind": "read", "config_key": "TIMEOUT", "line": 3}
                ],
                "entities": [],
                "is_test": False,
                "is_config": True,
            },
        }
    }
    plan = _Plan(
        question_type="configuration",
        search_terms=["api key settings"],
        retrieval_focus=["app.client.build_client"],
    )

    layer = retrieve_code_evidence(
        index,
        plan,
        "Where does the API_KEY configuration flow?",
        clone_root=tmp_path,
    )

    assert [citation.file_path for citation in layer.citations] == [
        "app/client.py",
        "app/settings.py",
    ]


def test_clone_root_drops_failed_slice_without_metadata_fallback(tmp_path, monkeypatch):
    from worker.fast_report_search import retrieve_code_evidence

    _install_fake_slice_extractor(monkeypatch, fail_paths={"app/missing.py"})
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "missing.py").write_text("source\n" * 5)
    index = {
        "files": {
            "app/missing.py": {
                "path": "app/missing.py",
                "tokens": ["missing"],
                "imports": [],
                "imported_by": [],
                "external_deps": [],
                "entities": [
                    {
                        "name": "missing",
                        "start_line": 1,
                        "end_line": 4,
                        "symbol_path": "app.missing.missing",
                    }
                ],
                "is_test": False,
                "is_config": False,
            }
        }
    }

    layer = retrieve_code_evidence(
        index,
        _Plan(question_type="implementation_location", search_terms=["missing"]),
        "Where is missing implemented?",
        clone_root=tmp_path,
    )

    assert layer.citations == []
    assert layer.evidence_blocks == []


def test_token_budget_eviction_drops_lowest_scored_slices(tmp_path, monkeypatch):
    from worker.fast_report_search import retrieve_code_evidence

    _install_fake_slice_extractor(monkeypatch)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "service.py").write_text("x" * 200 + "\n")
    index = {
        "files": {
            "app/service.py": {
                "path": "app/service.py",
                "tokens": ["service"],
                "imports": [],
                "imported_by": [],
                "external_deps": [],
                "entities": [
                    {
                        "name": "strong_alpha",
                        "start_line": 1,
                        "end_line": 1,
                        "signature": "def strong_alpha()",
                        "docstring": "alpha beta gamma delta",
                        "symbol_path": "app.service.strong_alpha",
                    },
                    {
                        "name": "weak_alpha",
                        "start_line": 1,
                        "end_line": 1,
                        "signature": "def weak_alpha()",
                        "docstring": "alpha",
                        "symbol_path": "app.service.weak_alpha",
                    },
                ],
                "is_test": False,
                "is_config": False,
            }
        }
    }

    layer = retrieve_code_evidence(
        index,
        _Plan(
            question_type="architecture",
            search_terms=["alpha", "beta", "gamma", "delta"],
        ),
        "Explain alpha beta gamma delta service.",
        clone_root=tmp_path,
        token_budget=60,
    )

    assert [block.symbol_path for block in layer.evidence_blocks] == [
        "app.service.strong_alpha"
    ]
