import pytest

from worker.pipeline.wiki_planner import (
    generate_page_plan,
    validate_page_plan,
)


async def test_generate_page_plan_returns_pages(mock_llm):
    module_tree = [
        {"path": ".", "files": ["main.py"]},
        {"path": "models", "files": ["models.py"]},
    ]
    plan = await generate_page_plan(module_tree, repo_name="testrepo", llm=mock_llm)
    assert len(plan.pages) >= 1
    assert all(hasattr(p, "title") for p in plan.pages)
    assert all(hasattr(p, "slug") for p in plan.pages)


async def test_generate_page_plan_with_enriched_context(mock_llm):
    module_tree = [
        {
            "path": ".",
            "files": ["main.py"],
            "file_count": 1,
            "class_count": 0,
            "function_count": 1,
            "classes": [],
            "functions": [{"name": "main"}],
            "summary": "main",
        },
        {
            "path": "models",
            "files": ["models.py"],
            "file_count": 1,
            "class_count": 2,
            "function_count": 0,
            "classes": [{"name": "User"}, {"name": "Post"}],
            "functions": [],
            "summary": "User, Post",
        },
    ]
    plan = await generate_page_plan(
        module_tree,
        repo_name="testrepo",
        llm=mock_llm,
        readme="# Test Repo\nA test project.",
        dep_summary={
            "models": {"depends_on": [], "depended_by": ["."], "external_deps": []}
        },
        clusters=[["main.py", "models.py"]],
    )
    assert len(plan.pages) >= 1
    # All pages should have descriptions from the updated mock
    assert all(p.description for p in plan.pages)


def test_validate_page_plan_accepts_valid():
    raw = {
        "pages": [
            {
                "title": "Overview",
                "slug": "overview",
                "modules": ["."],
                "description": "Project overview.",
            }
        ]
    }
    plan = validate_page_plan(raw)
    assert plan is not None
    assert plan.pages[0].slug == "overview"
    assert plan.pages[0].description == "Project overview."


def test_validate_page_plan_accepts_without_description():
    """Description is optional for backwards compatibility."""
    raw = {"pages": [{"title": "Overview", "slug": "overview", "modules": ["."]}]}
    plan = validate_page_plan(raw)
    assert plan.pages[0].description is None


def test_validate_page_plan_rejects_missing_slug():
    raw = {"pages": [{"title": "Overview", "modules": ["."]}]}
    with pytest.raises(ValueError):
        validate_page_plan(raw)


def test_validate_page_plan_rejects_empty_pages():
    with pytest.raises(ValueError):
        validate_page_plan({"pages": []})
