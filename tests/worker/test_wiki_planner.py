import pytest

from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
from worker.pipeline.wiki_planner import (
    WikiPageSpec,
    WikiPlan,
    _suggest_page_range,
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
                "primary_files": ["main.py", "README.md"],
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
                "primary_files": ["main.py"],
            },
            {
                "title": "Details",
                "purpose": "Detail page.",
                "parent": "NonExistentParent",
                "primary_files": ["details.py"],
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
                "primary_files": ["main.py"],
            }
        ]
    }
    all_files = ["main.py", "orphan.py", "also_orphan.py"]
    plan = validate_wiki_plan(raw, all_files=all_files)
    overview = plan.pages[0]
    assert "orphan.py" in overview.primary_files
    assert "also_orphan.py" in overview.primary_files


def test_wiki_page_spec_slug_unicode():
    spec = WikiPageSpec(title="中文文档", purpose="Chinese documentation.")
    assert spec.slug == "中文文档"

    spec2 = WikiPageSpec(title="API 接口", purpose="API interface.")
    assert spec2.slug == "api-接口"

    # Test symbols-only fallback to hash
    spec3 = WikiPageSpec(title="!!!", purpose="Symbols only.")
    assert spec3.slug.startswith("page-")
    assert len(spec3.slug) == 13  # "page-" (5) + hash (8)


def test_wiki_plan_to_wiki_json():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Overview",
                purpose="High-level overview.",
                primary_files=["main.py"],
            ),
            WikiPageSpec(
                title="API",
                purpose="API endpoints.",
                parent="Overview",
                primary_files=["api/main.py"],
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
                primary_files=["main.py", "README.md"],
            ),
            WikiPageSpec(
                title="Engine",
                purpose="Core engine.",
                parent="Overview",
                primary_files=["engine/core.py"],
            ),
        ]
    )
    internal = plan.to_internal_json()
    assert "repo_notes" in internal
    assert "pages" in internal
    overview = next(p for p in internal["pages"] if p["title"] == "Overview")
    assert overview["primary_files"] == ["main.py", "README.md"]
    assert overview["reference_files"] == []
    engine = next(p for p in internal["pages"] if p["title"] == "Engine")
    assert "engine/core.py" in engine["primary_files"]
    assert engine["reference_files"] == []
    assert engine.get("parent") == "Overview"
    # primary_files and reference_files must not be absent
    for page in internal["pages"]:
        assert "primary_files" in page
        assert "reference_files" in page


def test_validate_wiki_plan_duplicate_slugs_rejected():
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top.", "primary_files": ["a.py"]},
            # "Over view" slugifies to "over-view" — different from "overview"
            # but "Overview" and "overview" both slug to "overview"
            {"title": "Overview", "purpose": "Duplicate.", "primary_files": ["b.py"]},
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
                "primary_files": ["engine.py"],
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
                primary_files=["main.py"],
            ),
            WikiPageSpec(
                title="API Layer",
                purpose="REST API handlers.",
                parent="Overview",
                primary_files=["api/main.py"],
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


def test_suggest_page_range_small_repo():
    assert _suggest_page_range(5, 10) == (3, 6)


def test_suggest_page_range_boundary_file_count_10():
    # file_count=10 is the first value NOT in the small-repo branch (< 10)
    assert _suggest_page_range(10, 30) == (5, 12)
    assert _suggest_page_range(10, 50) == (8, 15)


def test_suggest_page_range_medium_repo_few_entities():
    assert _suggest_page_range(20, 30) == (5, 12)


def test_suggest_page_range_medium_repo_many_entities():
    assert _suggest_page_range(25, 80) == (8, 15)


def test_suggest_page_range_large_repo_few_entities():
    assert _suggest_page_range(60, 100) == (10, 25)


def test_suggest_page_range_large_repo_many_entities():
    assert _suggest_page_range(80, 200) == (15, 35)


def test_suggest_page_range_very_large_repo():
    assert _suggest_page_range(200, 500) == (20, 50)


def test_suggest_page_range_huge_repo():
    assert _suggest_page_range(500, 1000) == (30, 70)


async def test_generate_outline(mock_llm):
    """_generate_outline returns a list of page dicts with title/purpose/parent."""
    from worker.pipeline.wiki_planner import _generate_outline

    mock_llm.generate_structured.side_effect = None
    mock_llm.generate_structured.return_value = {
        "pages": [
            {"title": "Overview", "purpose": "Top-level overview."},
            {"title": "API", "purpose": "REST API.", "parent": "Overview"},
            {"title": "Worker", "purpose": "Background jobs.", "parent": "Overview"},
        ]
    }
    outline = await _generate_outline(
        file_summary="main.py: 0 classes, 1 functions [run]",
        repo_name="test",
        llm=mock_llm,
        readme="A test project.",
        dep_info=None,
        clusters=None,
        page_range=(3, 10),
        system="You are a planner.",
        on_retry=None,
    )
    assert len(outline) == 3
    assert outline[0]["title"] == "Overview"
    assert outline[1].get("parent") == "Overview"


async def test_assign_files(mock_llm):
    """_assign_files returns a dict mapping page titles to primary/reference file lists."""
    from worker.pipeline.wiki_planner import _assign_files

    mock_llm.generate_structured.side_effect = None
    mock_llm.generate_structured.return_value = {
        "assignments": [
            {"file": "main.py", "primary_page": "Overview", "reference_pages": ["API"]},
            {"file": "api.py", "primary_page": "API"},
            {"file": "worker.py", "primary_page": "Worker"},
        ]
    }
    outline = [
        {"title": "Overview", "purpose": "Top-level."},
        {"title": "API", "purpose": "REST API."},
        {"title": "Worker", "purpose": "Jobs."},
    ]
    result = await _assign_files(
        outline=outline,
        file_summary="main.py: ...\napi.py: ...\nworker.py: ...",
        dep_info=None,
        all_files=["main.py", "api.py", "worker.py"],
        llm=mock_llm,
        system="Assign files.",
        on_retry=None,
    )
    assert result["Overview"]["primary"] == ["main.py"]
    assert result["API"]["primary"] == ["api.py"]
    assert "main.py" in result["API"]["reference"]
    assert result["Worker"]["primary"] == ["worker.py"]


async def test_assign_files_orphans_distributed(mock_llm):
    """Files assigned to unknown pages get redistributed."""
    from worker.pipeline.wiki_planner import _assign_files

    mock_llm.generate_structured.side_effect = None
    mock_llm.generate_structured.return_value = {
        "assignments": [
            {"file": "main.py", "primary_page": "Overview"},
            {"file": "orphan.py", "primary_page": "NonExistent"},
        ]
    }
    outline = [{"title": "Overview", "purpose": "Top."}]
    result = await _assign_files(
        outline=outline,
        file_summary="main.py: ...\norphan.py: ...",
        dep_info=None,
        all_files=["main.py", "orphan.py"],
        llm=mock_llm,
        system="Assign.",
        on_retry=None,
    )
    # orphan.py should be assigned to Overview (first page)
    assert "orphan.py" in result["Overview"]["primary"]


def test_validate_rejects_page_over_25_files():
    raw = {
        "pages": [
            {
                "title": "Mega Page",
                "purpose": "Too many files.",
                "primary_files": [f"f{i}.py" for i in range(30)],
            },
        ]
    }
    with pytest.raises(ValueError, match="split into focused sub-pages"):
        validate_wiki_plan(raw)


def test_validate_rejects_empty_non_overview_page():
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top.", "primary_files": ["main.py"]},
            {"title": "Empty Page", "purpose": "Nothing here.", "primary_files": []},
        ]
    }
    with pytest.raises(ValueError, match="no primary files assigned"):
        validate_wiki_plan(raw)


def test_validate_allows_empty_overview_page():
    """Overview page with 0 files is allowed (orphans get assigned to it)."""
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top.", "primary_files": []},
            {"title": "API", "purpose": "Endpoints.", "primary_files": ["api.py"]},
        ]
    }
    plan = validate_wiki_plan(raw)
    assert len(plan.pages) == 2


def test_validate_rejects_too_deep_hierarchy():
    raw = {
        "pages": [
            {"title": "L0", "purpose": ".", "primary_files": ["a.py"]},
            {"title": "L1", "purpose": ".", "parent": "L0", "primary_files": ["b.py"]},
            {"title": "L2", "purpose": ".", "parent": "L1", "primary_files": ["c.py"]},
            {"title": "L3", "purpose": ".", "parent": "L2", "primary_files": ["d.py"]},
            {"title": "L4", "purpose": ".", "parent": "L3", "primary_files": ["e.py"]},
        ]
    }
    with pytest.raises(ValueError, match="flatten to at most 4 levels"):
        validate_wiki_plan(raw)


def test_validate_allows_4_level_hierarchy():
    """Hierarchy at exactly 4 levels deep should pass."""
    raw = {
        "pages": [
            {"title": "L0", "purpose": ".", "primary_files": ["a.py"]},
            {"title": "L1", "purpose": ".", "parent": "L0", "primary_files": ["b.py"]},
            {"title": "L2", "purpose": ".", "parent": "L1", "primary_files": ["c.py"]},
            {"title": "L3", "purpose": ".", "parent": "L2", "primary_files": ["d.py"]},
        ]
    }
    plan = validate_wiki_plan(raw)
    assert len(plan.pages) == 4


def test_validate_rejects_flat_plan_for_large_repo():
    page1_files = [f"f{i}.py" for i in range(20)]
    page2_files = [f"g{i}.py" for i in range(15)]
    raw = {
        "pages": [
            {"title": "Page1", "purpose": ".", "primary_files": page1_files},
            {"title": "Page2", "purpose": ".", "primary_files": page2_files},
        ]
    }
    all_files = [f"f{i}.py" for i in range(20)] + [f"g{i}.py" for i in range(15)]
    with pytest.raises(ValueError, match="create 2-3 levels of hierarchy"):
        validate_wiki_plan(raw, all_files=all_files)


def test_validate_rejects_too_few_pages():
    many_files = [f"f{i}.py" for i in range(25)]
    raw = {
        "pages": [
            {"title": "Overview", "purpose": ".", "primary_files": many_files},
        ]
    }
    with pytest.raises(ValueError, match="create more granular pages"):
        validate_wiki_plan(raw, page_range=(5, 20))


async def test_generate_wiki_plan_two_phase(mock_llm):
    """generate_wiki_plan uses two-phase planning."""
    # Phase 1 returns outline, Phase 2 returns assignments
    mock_llm.generate_structured.side_effect = [
        # Phase 1: outline
        {
            "pages": [
                {"title": "Overview", "purpose": "Top-level overview."},
                {"title": "Models", "purpose": "Data models."},
                {"title": "Utilities", "purpose": "Utility helpers."},
            ]
        },
        # Phase 2: file assignment
        {
            "assignments": [
                {"file": "main.py", "primary_page": "Overview"},
                {"file": "models.py", "primary_page": "Models"},
                {"file": "utils.py", "primary_page": "Utilities"},
            ]
        },
    ]

    file_analysis = FileAnalysis(
        files={
            "main.py": FileInfo(rel_path="main.py", entities=[], summary=""),
            "models.py": FileInfo(rel_path="models.py", entities=[], summary=""),
            "utils.py": FileInfo(rel_path="utils.py", entities=[], summary=""),
        }
    )
    plan = await generate_wiki_plan(file_analysis, repo_name="test", llm=mock_llm)
    assert len(plan.pages) == 3
    assert {p.title for p in plan.pages} == {"Overview", "Models", "Utilities"}
    titles = [p.title for p in plan.pages]
    assert plan.pages[titles.index("Overview")].primary_files == ["main.py"]
    assert plan.pages[titles.index("Models")].primary_files == ["models.py"]
    assert plan.pages[titles.index("Utilities")].primary_files == ["utils.py"]
