from worker.fast_report.planning import (
    QUESTION_TYPES,
    ExpansionGraph,
    QuestionTypeProfile,
    build_plan_prompt_context,
    expansion_graph_for,
    profile_for_question_type,
)


def test_question_types_enum_is_eight_values():
    assert QUESTION_TYPES == (
        "architecture",
        "execution_flow",
        "dependency",
        "error_handling",
        "configuration",
        "testing",
        "implementation_location",
        "unknown",
    )


def test_profile_for_question_type_returns_exact_spec_table():
    assert {
        question_type: profile_for_question_type(question_type)
        for question_type in QUESTION_TYPES
    } == {
        "architecture": QuestionTypeProfile(4, 3, 12, 50_000, 40, 3),
        "execution_flow": QuestionTypeProfile(3, 3, 10, 50_000, 50, 2),
        "dependency": QuestionTypeProfile(3, 2, 10, 40_000, 30, 1),
        "error_handling": QuestionTypeProfile(2, 2, 8, 35_000, 40, 2),
        "configuration": QuestionTypeProfile(3, 2, 8, 35_000, 30, 2),
        "testing": QuestionTypeProfile(2, 1, 6, 40_000, 60, 2),
        "implementation_location": QuestionTypeProfile(2, 1, 4, 25_000, 200, 1),
        "unknown": QuestionTypeProfile(2, 2, 6, 40_000, 50, 1),
    }


def test_profile_for_unknown_question_type_uses_default():
    assert profile_for_question_type("not_a_real_type") == profile_for_question_type(
        "unknown"
    )


def test_expansion_graph_for_question_type_returns_exact_spec_table():
    assert {
        question_type: expansion_graph_for(question_type)
        for question_type in QUESTION_TYPES
    } == {
        "architecture": ExpansionGraph("imports_and_imported_by", "sibling_directory"),
        "execution_flow": ExpansionGraph("call_sites", "imports"),
        "dependency": ExpansionGraph(
            "imports_and_imported_by", "external_deps_overlap"
        ),
        "error_handling": ExpansionGraph("exception_touchpoints", "imports"),
        "configuration": ExpansionGraph("config_touchpoints", "is_config_files"),
        "testing": ExpansionGraph("sibling_token_overlap", "imports"),
        "implementation_location": ExpansionGraph("imports", None),
        "unknown": ExpansionGraph("imports_and_imported_by", None),
    }


def test_expansion_graph_for_unknown_question_type_uses_default():
    assert expansion_graph_for("totally_unknown") == expansion_graph_for("unknown")


def test_build_plan_prompt_context_includes_directory_tree_hubs_headings():
    index = {
        "directory_tree": "src/\n  main.py\n",
        "hub_modules": [{"path": "src/util.py", "in_degree": 5, "purpose": "Util."}],
        "readme_headings": [
            "A",
            "B",
            "C",
            "D",
            "E",
            "F",
            "G",
            "H",
            "I",
            "J",
            "K",
            "L",
            "M",
            "N",
        ],
    }
    ctx = build_plan_prompt_context(index)

    assert "Directory tree:" in ctx
    assert "src/\n  main.py" in ctx
    assert "src/util.py" in ctx
    assert "in_degree=5" in ctx
    assert "Symbol path convention" in ctx
    readme_block = ctx.split("README headings:", 1)[1].split("Hub modules:", 1)[0]
    assert readme_block.count("\n- ") == 12
    assert "- L" in readme_block
    assert "- M" not in readme_block
