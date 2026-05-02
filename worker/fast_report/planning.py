"""Fast report planner inputs, question type profiles, and repo-shape context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worker.pipeline.retrieval.repo_search import register_expansion_profile

QUESTION_TYPES = (
    "architecture",
    "execution_flow",
    "dependency",
    "error_handling",
    "configuration",
    "testing",
    "implementation_location",
    "unknown",
)

_README_HEADINGS_PROMPT_LIMIT = 12


@dataclass(frozen=True, slots=True)
class QuestionTypeProfile:
    seed: int
    depth: int
    result_limit: int
    code_evidence_token_budget: int
    per_slice_line_cap: int
    slices_per_file: int


@dataclass(frozen=True, slots=True)
class ExpansionGraph:
    primary: str
    secondary: str | None


_PROFILES: dict[str, QuestionTypeProfile] = {
    "architecture": QuestionTypeProfile(4, 3, 12, 50_000, 40, 3),
    "execution_flow": QuestionTypeProfile(3, 3, 10, 50_000, 50, 2),
    "dependency": QuestionTypeProfile(3, 2, 10, 40_000, 30, 1),
    "error_handling": QuestionTypeProfile(2, 2, 8, 35_000, 40, 2),
    "configuration": QuestionTypeProfile(3, 2, 8, 35_000, 30, 2),
    "testing": QuestionTypeProfile(2, 1, 6, 40_000, 60, 2),
    "implementation_location": QuestionTypeProfile(2, 1, 4, 25_000, 200, 1),
    "unknown": QuestionTypeProfile(2, 2, 6, 40_000, 50, 1),
}

_EXPANSION_GRAPHS: dict[str, ExpansionGraph] = {
    "architecture": ExpansionGraph("imports_and_imported_by", "sibling_directory"),
    "execution_flow": ExpansionGraph("call_sites", "imports"),
    "dependency": ExpansionGraph("imports_and_imported_by", "external_deps_overlap"),
    "error_handling": ExpansionGraph("exception_touchpoints", "imports"),
    "configuration": ExpansionGraph("config_touchpoints", "is_config_files"),
    "testing": ExpansionGraph("sibling_token_overlap", "imports"),
    "implementation_location": ExpansionGraph("imports", None),
    "unknown": ExpansionGraph("imports_and_imported_by", None),
}

for _question_type, _expansion_graph in _EXPANSION_GRAPHS.items():
    register_expansion_profile(_question_type, _expansion_graph)


def profile_for_question_type(question_type: str) -> QuestionTypeProfile:
    return _PROFILES.get(question_type, _PROFILES["unknown"])


def expansion_graph_for(question_type: str) -> ExpansionGraph:
    return _EXPANSION_GRAPHS.get(question_type, _EXPANSION_GRAPHS["unknown"])


def build_plan_prompt_context(index: dict[str, Any]) -> str:
    directory_tree = str(index.get("directory_tree") or "").rstrip()
    readme_headings = list(index.get("readme_headings") or [])[
        :_README_HEADINGS_PROMPT_LIMIT
    ]
    hub_modules = list(index.get("hub_modules") or [])

    heading_lines = "\n".join(f"- {heading}" for heading in readme_headings)
    if not heading_lines:
        heading_lines = "- (no README headings detected)"

    hub_lines: list[str] = []
    for hub in hub_modules:
        path = str(hub.get("path") or "")
        if not path:
            continue
        purpose = str(hub.get("purpose") or "").strip()
        in_degree = hub.get("in_degree", 0)
        line = f"- {path} (in_degree={in_degree})"
        if purpose:
            line = f"{line}: {purpose}"
        hub_lines.append(line)
    hubs_block = "\n".join(hub_lines) or "- (no hub modules detected)"

    return (
        f"Directory tree:\n{directory_tree or '(empty)'}\n\n"
        f"README headings:\n{heading_lines}\n\n"
        f"Hub modules:\n{hubs_block}\n\n"
        "Symbol path convention: Use `module.path.symbol_name` for retrieval_focus "
        "(path slashes become dots and the extension is stripped)."
    )
