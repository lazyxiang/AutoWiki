import pytest
from unittest.mock import AsyncMock
from worker.pipeline.wiki_planner import generate_page_plan, validate_page_plan, PagePlan


async def test_generate_page_plan_returns_pages(mock_llm):
    module_tree = [
        {"path": ".", "files": ["main.py"]},
        {"path": "models", "files": ["models.py"]},
    ]
    plan = await generate_page_plan(module_tree, repo_name="testrepo", llm=mock_llm)
    assert len(plan.pages) >= 1
    assert all(hasattr(p, "title") for p in plan.pages)
    assert all(hasattr(p, "slug") for p in plan.pages)


def test_validate_page_plan_accepts_valid():
    raw = {"pages": [{"title": "Overview", "slug": "overview", "modules": ["."]}]}
    plan = validate_page_plan(raw)
    assert plan is not None
    assert plan.pages[0].slug == "overview"


def test_validate_page_plan_rejects_missing_slug():
    raw = {"pages": [{"title": "Overview", "modules": ["."]}]}
    with pytest.raises(ValueError):
        validate_page_plan(raw)


def test_validate_page_plan_rejects_empty_pages():
    with pytest.raises(ValueError):
        validate_page_plan({"pages": []})
