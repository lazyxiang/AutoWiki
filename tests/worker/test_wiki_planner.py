import pytest

from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
from worker.pipeline.wiki_planner import (
    WikiPageSpec,
    WikiPlan,
    generate_wiki_plan,
    validate_wiki_plan,
)


def _make_file_analysis():
    return FileAnalysis(
        files={
            "main.py": FileInfo(
                rel_path="main.py",
                entities=[],
                class_count=0,
                function_count=0,
                summary="",
            ),
        }
    )


async def test_generate_wiki_plan(mock_llm):
    file_analysis = _make_file_analysis()
    plan = await generate_wiki_plan(file_analysis, repo_name="testrepo", llm=mock_llm)

    assert isinstance(plan, WikiPlan)
    assert len(plan.pages) >= 1
    for p in plan.pages:
        assert isinstance(p, WikiPageSpec)
        assert hasattr(p, "purpose")
        assert hasattr(p, "title")
        # slug is a property derived from title
        assert isinstance(p.slug, str)
        assert len(p.slug) > 0


def test_validate_wiki_plan_basic():
    raw = {
        "pages": [
            {
                "title": "Overview",
                "purpose": "High-level overview of the project.",
                "files": ["main.py", "README.md"],
            }
        ]
    }
    plan = validate_wiki_plan(raw)
    assert plan is not None
    assert isinstance(plan, WikiPlan)
    assert plan.pages[0].title == "Overview"
    assert plan.pages[0].purpose == "High-level overview of the project."
    assert plan.pages[0].slug == "overview"


def test_validate_wiki_plan_invalid_parent_dropped():
    raw = {
        "pages": [
            {
                "title": "Overview",
                "purpose": "Top level page.",
                "files": ["main.py"],
            },
            {
                "title": "Details",
                "purpose": "Detail page.",
                "parent": "NonExistentParent",
                "files": ["details.py"],
            },
        ]
    }
    plan = validate_wiki_plan(raw)
    details_page = next(p for p in plan.pages if p.title == "Details")
    assert details_page.parent is None


def test_validate_wiki_plan_orphan_files():
    raw = {
        "pages": [
            {
                "title": "Overview",
                "purpose": "Top level page.",
                "files": ["main.py"],
            }
        ]
    }
    all_files = ["main.py", "orphan.py", "also_orphan.py"]
    plan = validate_wiki_plan(raw, all_files=all_files)
    overview = plan.pages[0]
    assert "orphan.py" in overview.files
    assert "also_orphan.py" in overview.files


def test_wiki_page_spec_slug():
    spec = WikiPageSpec(title="My Cool Component", purpose="Handles cool stuff.")
    assert spec.slug == "my-cool-component"

    spec2 = WikiPageSpec(title="API Endpoints", purpose="REST handlers.")
    assert spec2.slug == "api-endpoints"

    spec3 = WikiPageSpec(title="Overview", purpose="High-level overview.")
    assert spec3.slug == "overview"


def test_wiki_plan_to_wiki_json():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Overview",
                purpose="High-level overview.",
                files=["main.py"],
            ),
            WikiPageSpec(
                title="API",
                purpose="API endpoints.",
                parent="Overview",
                files=["api/main.py"],
            ),
        ]
    )
    wiki_json = plan.to_wiki_json()
    assert "pages" in wiki_json
    assert "repo_notes" in wiki_json
    for page in wiki_json["pages"]:
        assert "files" not in page
        assert "slug" not in page
        assert "title" in page
        assert "purpose" in page
    # child page preserves parent title
    api_page = next(p for p in wiki_json["pages"] if p["title"] == "API")
    assert api_page.get("parent") == "Overview"
    # root page has no parent key
    overview = next(p for p in wiki_json["pages"] if p["title"] == "Overview")
    assert "parent" not in overview


def test_wiki_plan_to_internal_json():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Overview",
                purpose="Top-level page.",
                files=["main.py", "README.md"],
            ),
            WikiPageSpec(
                title="Engine",
                purpose="Core engine.",
                parent="Overview",
                files=["engine/core.py"],
            ),
        ]
    )
    internal = plan.to_internal_json()
    assert "repo_notes" in internal
    assert "pages" in internal
    overview = next(p for p in internal["pages"] if p["title"] == "Overview")
    assert overview["files"] == ["main.py", "README.md"]
    engine = next(p for p in internal["pages"] if p["title"] == "Engine")
    assert "engine/core.py" in engine["files"]
    assert engine.get("parent") == "Overview"
    # files must not be absent
    for page in internal["pages"]:
        assert "files" in page


def test_validate_wiki_plan_duplicate_slugs_rejected():
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top.", "files": ["a.py"]},
            # "Over view" slugifies to "over-view" — different from "overview"
            # but "Overview" and "overview" both slug to "overview"
            {"title": "Overview", "purpose": "Duplicate.", "files": ["b.py"]},
        ]
    }
    with pytest.raises(ValueError, match="Duplicate page slugs"):
        validate_wiki_plan(raw)


def test_validate_wiki_plan_existing_titles_keeps_cross_slice_parent():
    """A parent that lives outside the partial refresh slice should not be dropped."""
    raw = {
        "pages": [
            {
                "title": "Engine",
                "purpose": "Core engine.",
                "parent": "Overview",  # Overview is NOT in this partial batch
                "files": ["engine.py"],
            }
        ]
    }
    # Without existing_titles, "Overview" parent would be dropped
    plan_no_ctx = validate_wiki_plan(raw)
    assert plan_no_ctx.pages[0].parent is None

    # With existing_titles, the parent is preserved
    plan_with_ctx = validate_wiki_plan(raw, existing_titles={"Overview"})
    assert plan_with_ctx.pages[0].parent == "Overview"


def test_wiki_page_spec_parent_slug():
    spec = WikiPageSpec(
        title="Sub Page", purpose="Child.", parent="Engine Architecture"
    )
    assert spec.parent_slug == "engine-architecture"

    spec_no_parent = WikiPageSpec(title="Root", purpose="Root page.")
    assert spec_no_parent.parent_slug is None


def test_wiki_plan_to_api_structure():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Overview",
                purpose="High-level overview.",
                files=["main.py"],
            ),
            WikiPageSpec(
                title="API Layer",
                purpose="REST API handlers.",
                parent="Overview",
                files=["api/main.py"],
            ),
        ]
    )
    api_struct = plan.to_api_structure()
    assert "pages" in api_struct
    pages = api_struct["pages"]
    assert len(pages) == 2

    overview = next(p for p in pages if p["title"] == "Overview")
    assert "slug" in overview
    assert overview["slug"] == "overview"
    assert "parent_slug" in overview
    assert overview["parent_slug"] is None
    assert "description" in overview
    assert overview["description"] == "High-level overview."

    api_page = next(p for p in pages if p["title"] == "API Layer")
    assert api_page["slug"] == "api-layer"
    assert api_page["parent_slug"] == "overview"
