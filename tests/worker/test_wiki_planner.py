import pytest

from worker.pipeline import wiki_planner as wp
from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
from worker.pipeline.wiki_planner import (
    WikiPageSpec,
    WikiPlan,
    _enforce_ownership,
    _heuristic_select_files,
    _prefilter_candidates,
    _score_file_for_page,
    _suggest_page_range,
    _validate_outline_structure,
    _validate_selections,
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
            "models.py": FileInfo(
                rel_path="models.py",
                entities=[],
                class_count=0,
                function_count=0,
                summary="",
            ),
            "utils.py": FileInfo(
                rel_path="utils.py",
                entities=[],
                class_count=0,
                function_count=0,
                summary="",
            ),
        }
    )


def _selected(path: str, relevance: int = 8) -> dict[str, object]:
    return {"path": path, "relevance": relevance}


async def test_generate_wiki_plan(mock_llm):
    mock_llm.generate_structured.side_effect = [
        {
            "pages": [
                {"title": "Core", "purpose": "Core subsystem."},
                {"title": "Infra", "purpose": "Infrastructure subsystem."},
                {"title": "Models", "purpose": "Data models.", "parent": "Core"},
                {"title": "Utils", "purpose": "Utility helpers.", "parent": "Infra"},
            ]
        },
        {
            "selections": [
                {"page_title": "Models", "files": [_selected("models.py", 8)]},
                {"page_title": "Utils", "files": [_selected("utils.py", 7)]},
            ]
        },
    ]
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
    # Two top-level subsystems + one child with a dangling parent reference.
    # The dangling parent is dropped (page becomes top-level), which is the
    # behavior under test; max_depth stays 2 because Overview has a valid parent.
    raw = {
        "pages": [
            {
                "title": "Core",
                "purpose": "Core subsystem.",
                "files": ["main.py"],
            },
            {
                "title": "API",
                "purpose": "API subsystem.",
                "files": ["api.py"],
            },
            {
                "title": "Overview",
                "purpose": "Overview page.",
                "parent": "Core",
                "files": ["overview.py"],
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


def test_validate_unselected_files_do_not_raise():
    """Selection model: unselected files are logged but never raise."""
    raw = {
        "pages": [
            {
                "title": "Overview",
                "purpose": "Top level page.",
                "files": ["main.py"],
            }
        ]
    }
    # a/core.py is unselected — no error in selection model
    all_files = ["main.py", "a/core.py"]
    plan = validate_wiki_plan(raw, all_files=all_files)
    assert len(plan.pages) == 1

    # tests/test_a.py also unselected — should still pass
    all_files_pass = ["main.py", "tests/test_a.py"]
    plan2 = validate_wiki_plan(raw, all_files=all_files_pass)
    assert len(plan2.pages) == 1
    assert "tests/test_a.py" not in plan2.pages[0].files


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
            {"title": "Core", "purpose": "Core subsystem."},
            {"title": "Infra", "purpose": "Infrastructure subsystem."},
            {"title": "API", "purpose": "REST API.", "parent": "Core"},
            {"title": "Worker", "purpose": "Background jobs.", "parent": "Infra"},
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
    assert len(outline) == 4
    assert outline[0]["title"] == "Core"
    assert outline[2].get("parent") == "Core"


async def test_assign_files(mock_llm):
    """_select_files returns a dict mapping page titles to file lists."""
    from worker.pipeline.wiki_planner import _select_files

    mock_llm.generate_structured.side_effect = None
    mock_llm.generate_structured.return_value = {
        "selections": [
            {"page_title": "Overview", "files": [_selected("main.py", 8)]},
            {"page_title": "API", "files": [_selected("api.py", 7)]},
            {"page_title": "Worker", "files": [_selected("worker.py", 6)]},
        ]
    }
    outline = [
        {"title": "Overview", "purpose": "Top-level."},
        {"title": "API", "purpose": "REST API."},
        {"title": "Worker", "purpose": "Jobs."},
    ]
    result = await _select_files(
        outline=outline,
        file_summary="main.py: ...\napi.py: ...\nworker.py: ...",
        dep_info=None,
        all_files=["main.py", "api.py", "worker.py"],
        file_infos={},
        dep_graph=None,
        llm=mock_llm,
        system="Select files.",
        on_retry=None,
    )
    assert result["Overview"] == ["main.py"]
    assert result["API"] == ["api.py"]
    assert result["Worker"] == ["worker.py"]


async def test_assign_files_orphans_distributed(mock_llm):
    """Files not in any valid page are silently ignored (page-centric model)."""
    from worker.pipeline.wiki_planner import _select_files

    mock_llm.generate_structured.side_effect = None
    mock_llm.generate_structured.return_value = {
        "selections": [
            {"page_title": "Overview", "files": [_selected("main.py", 8)]},
            # "orphan.py" omitted — page-centric model doesn't need to assign it
        ]
    }
    outline = [{"title": "Overview", "purpose": "Top."}]
    result = await _select_files(
        outline=outline,
        file_summary="main.py: ...\norphan.py: ...",
        dep_info=None,
        all_files=["main.py", "orphan.py"],
        file_infos={},
        dep_graph=None,
        llm=mock_llm,
        system="Select.",
        on_retry=None,
    )
    # page-centric: Overview gets main.py; orphan.py is simply not selected
    assert "main.py" in result["Overview"]


def test_validate_rejects_page_over_50_files():
    raw = {
        "pages": [
            {
                "title": "Mega Page",
                "purpose": "Too many files.",
                "files": [f"f{i}.py" for i in range(55)],
            },
        ]
    }
    with pytest.raises(ValueError, match="is overloaded"):
        validate_wiki_plan(raw)


def test_validate_rejects_empty_non_overview_leaf_page():
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top.", "files": ["main.py"]},
            {"title": "Empty Leaf", "purpose": "Nothing here.", "files": []},
        ]
    }
    with pytest.raises(ValueError, match="has no files assigned"):
        validate_wiki_plan(raw)


def test_validate_allows_empty_parent_page():
    """Empty pages are allowed if they have children."""
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top.", "files": ["main.py"]},
            {"title": "Category", "purpose": "A container.", "files": []},
            {
                "title": "Child",
                "purpose": ".",
                "parent": "Category",
                "files": ["child.py"],
            },
        ]
    }
    plan = validate_wiki_plan(raw)
    assert len(plan.pages) == 3


def test_validate_allows_empty_overview_page():
    """Overview page with 0 files is allowed (parent pages need no files)."""
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top.", "files": []},
            {"title": "Backend", "purpose": "Backend subsystem.", "files": []},
            {
                "title": "API",
                "purpose": "Endpoints.",
                "parent": "Overview",
                "files": ["api.py"],
            },
            {
                "title": "Core",
                "purpose": "Core logic.",
                "parent": "Backend",
                "files": ["core.py"],
            },
        ]
    }
    plan = validate_wiki_plan(raw)
    assert len(plan.pages) == 4


def test_validate_rejects_too_deep_hierarchy():
    raw = {
        "pages": [
            {"title": "L0", "purpose": ".", "files": ["a.py"]},
            {"title": "L1", "purpose": ".", "parent": "L0", "files": ["b.py"]},
            {"title": "L2", "purpose": ".", "parent": "L1", "files": ["c.py"]},
        ]
    }
    with pytest.raises(ValueError, match="use exactly 2 levels"):
        validate_wiki_plan(raw)


def test_validate_allows_2_level_hierarchy():
    """Hierarchy at exactly 2 levels deep should pass."""
    raw = {
        "pages": [
            {"title": "L0", "purpose": ".", "files": ["a.py"]},
            {"title": "L1", "purpose": ".", "parent": "L0", "files": ["b.py"]},
        ]
    }
    plan = validate_wiki_plan(raw)
    assert len(plan.pages) == 2


def test_validate_rejects_flat_plan_for_large_repo():
    # Any multi-page plan with all pages at top level must be rejected.
    page1_files = [f"f{i}.py" for i in range(10)]
    page2_files = [f"g{i}.py" for i in range(10)]
    raw = {
        "pages": [
            {"title": "Page1", "purpose": ".", "files": page1_files},
            {"title": "Page2", "purpose": ".", "files": page2_files},
        ]
    }
    all_files = [f"f{i}.py" for i in range(20)] + [f"g{i}.py" for i in range(15)]
    with pytest.raises(ValueError, match="All pages are top-level"):
        validate_wiki_plan(raw, all_files=all_files)


def test_validate_rejects_too_few_pages():
    # Valid 2-level structure but fewer pages than the required minimum.
    raw = {
        "pages": [
            {"title": "Core", "purpose": ".", "files": []},
            {"title": "Infra", "purpose": ".", "files": []},
            {
                "title": "API",
                "purpose": ".",
                "parent": "Core",
                "files": [f"f{i}.py" for i in range(5)],
            },
            {
                "title": "Utils",
                "purpose": ".",
                "parent": "Infra",
                "files": [f"g{i}.py" for i in range(5)],
            },
        ]
    }
    with pytest.raises(ValueError, match="create more granular pages"):
        validate_wiki_plan(raw, page_range=(5, 20))


async def test_generate_outline_logs_each_validation_failure(caplog):
    """Each retry of _generate_outline must log a WARNING with the error."""
    import logging
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _generate_outline

    # First two calls return an invalid outline (duplicate slug → validation fails),
    # third call returns a valid outline.
    bad = {
        "pages": [
            {"title": "Overview", "purpose": "a"},
            {"title": "overview", "purpose": "b"},  # dup slug
        ]
    }
    good = {
        "pages": [
            {"title": "Core", "purpose": "Core subsystem"},
            {"title": "API", "purpose": "API subsystem"},
            {"title": "Models", "purpose": "Data models", "parent": "Core"},
            {"title": "Utils", "purpose": "Helpers", "parent": "Core"},
            {"title": "Routes", "purpose": "HTTP routes", "parent": "API"},
        ]
    }
    llm = AsyncMock()
    llm.generate_structured.side_effect = [bad, bad, good]

    with caplog.at_level(logging.WARNING, logger="worker.planner"):
        pages = await _generate_outline(
            file_summary="files",
            repo_name="repo",
            llm=llm,
            readme=None,
            dep_info=None,
            clusters=None,
            page_range=(5, 20),
            system="sys",
            on_retry=None,
            max_retries=3,
            total_file_count=50,
        )

    assert len(pages) == 5
    retry_logs = [r for r in caplog.records if "wiki_planner.outline" in r.getMessage()]
    assert len(retry_logs) == 2  # two failures logged, third succeeded
    assert all("attempt" in r.getMessage() for r in retry_logs)
    assert all("Duplicate page slugs" in r.getMessage() for r in retry_logs)


async def test_generate_outline_logs_en_keywords_required_retry(caplog):
    """CJK outline pages without en_keywords should emit dedicated telemetry."""
    import logging
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _generate_outline

    bad = {
        "pages": [
            {"title": "前端应用", "purpose": "介绍 Next.js 应用架构。"},
            {"title": "Backend", "purpose": "Backend subsystem."},
            {"title": "Components", "purpose": "组件库。", "parent": "前端应用"},
        ]
    }
    good = {
        "pages": [
            {
                "title": "前端应用",
                "purpose": "介绍 Next.js 应用架构。",
                "en_keywords": ["web", "app", "next"],
            },
            {"title": "Backend", "purpose": "Backend subsystem."},
            {
                "title": "Components",
                "purpose": "组件库。",
                "parent": "前端应用",
                "en_keywords": ["web", "components", "next"],
            },
        ]
    }
    llm = AsyncMock()
    llm.generate_structured.side_effect = [bad, good]

    with caplog.at_level(logging.WARNING, logger="worker.planner"):
        pages = await _generate_outline(
            file_summary="files",
            repo_name="repo",
            llm=llm,
            readme=None,
            dep_info=None,
            clusters=None,
            page_range=(1, 10),
            system="sys",
            on_retry=None,
            max_retries=2,
            total_file_count=10,
        )

    assert pages == good["pages"]
    assert any(
        "wiki_planner.en_keywords_required" in record.getMessage()
        for record in caplog.records
    )
    assert any(
        "wiki_planner.outline" in record.getMessage() for record in caplog.records
    )


async def test_generate_outline_logs_en_keywords_required_final_failure(caplog):
    """Exhausted CJK en_keywords failures should keep dedicated telemetry."""
    import logging
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import WikiPlannerError, _generate_outline

    bad = {
        "pages": [
            {"title": "前端应用", "purpose": "介绍 Next.js 应用架构。"},
            {"title": "Backend", "purpose": "Backend subsystem."},
            {"title": "Components", "purpose": "组件库。", "parent": "前端应用"},
        ]
    }
    llm = AsyncMock()
    llm.generate_structured.side_effect = [bad, bad]

    with caplog.at_level(logging.WARNING, logger="worker.planner"):
        with pytest.raises(WikiPlannerError, match="en_keywords"):
            await _generate_outline(
                file_summary="files",
                repo_name="repo",
                llm=llm,
                readme=None,
                dep_info=None,
                clusters=None,
                page_range=(1, 10),
                system="sys",
                on_retry=None,
                max_retries=2,
                total_file_count=10,
            )

    final_logs = [
        record for record in caplog.records if record.levelno >= logging.ERROR
    ]
    assert any(
        "wiki_planner.en_keywords_required" in record.getMessage()
        for record in final_logs
    )
    assert any("wiki_planner.outline" in record.getMessage() for record in final_logs)


async def test_generate_wiki_plan_two_phase(mock_llm):
    """generate_wiki_plan uses two-phase planning."""
    # Phase 1 returns outline, Phase 2 returns assignments
    mock_llm.generate_structured.side_effect = [
        # Phase 1: outline — 2-level hierarchy
        {
            "pages": [
                {"title": "Core", "purpose": "Core subsystem."},
                {"title": "Infra", "purpose": "Infrastructure subsystem."},
                {"title": "Models", "purpose": "Data models.", "parent": "Core"},
                {"title": "Utils", "purpose": "Utility helpers.", "parent": "Infra"},
            ]
        },
        # Phase 2: file selection
        {
            "selections": [
                {
                    "page_title": "Models",
                    "files": [_selected("main.py", 8), _selected("models.py", 7)],
                },
                {"page_title": "Utils", "files": [_selected("utils.py", 8)]},
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
    assert len(plan.pages) == 4
    assert {p.title for p in plan.pages} == {"Core", "Infra", "Models", "Utils"}
    titles = [p.title for p in plan.pages]
    assert plan.pages[titles.index("Utils")].files == ["utils.py"]


async def test_generate_wiki_plan_scales_summary_budget_for_large_repos(
    mock_llm, monkeypatch
):
    """Phase 1 gives larger repos a larger explicit summary budget."""
    from worker.pipeline import wiki_planner as wp

    class TrackingFileAnalysis(FileAnalysis):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.summary_kwargs = None

        def to_llm_summary(self, **kwargs):
            self.summary_kwargs = kwargs
            return "tracked summary"

    async def fake_generate_outline(**kwargs):
        return [{"title": "Overview", "purpose": "Overview."}]

    async def fake_select_files(**kwargs):
        return {"Overview": [next(iter(kwargs["all_files"]))]}

    def fake_validate_wiki_plan(*args, **kwargs):
        return WikiPlan(pages=[WikiPageSpec(title="Overview", purpose="Overview.")])

    monkeypatch.setattr(wp, "_generate_outline", fake_generate_outline)
    monkeypatch.setattr(wp, "_select_files", fake_select_files)
    monkeypatch.setattr(wp, "validate_wiki_plan", fake_validate_wiki_plan)

    def make_analysis(paths: list[str]) -> TrackingFileAnalysis:
        return TrackingFileAnalysis(
            files={
                path: FileInfo(rel_path=path, entities=[], summary="") for path in paths
            }
        )

    small_repo = make_analysis(["main.py", "models.py", "utils.py"])
    large_repo = make_analysis([f"pkg/mod{i}.py" for i in range(1000)])

    await generate_wiki_plan(small_repo, repo_name="small", llm=mock_llm)
    await generate_wiki_plan(large_repo, repo_name="large", llm=mock_llm)

    assert small_repo.summary_kwargs["max_files"] >= len(small_repo.files)
    assert (
        large_repo.summary_kwargs["max_files"] > small_repo.summary_kwargs["max_files"]
    )


async def test_assign_files_logs_each_validation_failure_and_feedback(caplog):
    """_select_files must log each retry AND throw on final failure.

    With max_retries=2, the batched path does 2 attempts before throwing.
    """
    import logging
    from unittest.mock import AsyncMock

    from worker.pipeline.wiki_planner import _select_files

    outline = [
        {"title": "Overview", "purpose": "top"},
        {"title": "Core", "purpose": "core"},
    ]
    # stuffed selection: only Overview has files, Core is empty (fails validation)
    stuffed = {
        "selections": [{"page_title": "Overview", "files": [_selected("main.py", 8)]}]
    }
    llm = AsyncMock()
    # Two batched calls will both return a stuffed response that fails validation.
    llm.generate_structured.side_effect = [stuffed, stuffed]

    with caplog.at_level(logging.WARNING, logger="worker.planner"):
        with pytest.raises(ValueError, match="Failed to select files"):
            await _select_files(
                outline=outline,
                file_summary="files",
                dep_info=None,
                all_files=["main.py"],
                file_infos={},
                dep_graph=None,
                llm=llm,
                system="sys",
                on_retry=None,
                max_retries=2,
            )

    retry_logs = [
        r
        for r in caplog.records
        if "wiki_planner.select_files" in r.getMessage()
        and "attempt" in r.getMessage()
        and r.levelno == logging.WARNING
    ]
    fallback_logs = [
        r
        for r in caplog.records
        if "wiki_planner.select_files" in r.getMessage()
        and "all retries exhausted" in r.getMessage()
        and r.levelno == logging.ERROR
    ]
    assert len(retry_logs) == 1, f"expected 1 retry log, got {len(retry_logs)}"
    assert len(fallback_logs) == 1, (
        f"expected 1 fallback error, got {len(fallback_logs)}"
    )


async def test_generate_wiki_plan_phase2_recovery(mock_llm):
    """When Phase 2 (assignment) fails, generate_wiki_plan must recover
    using score-based heuristic selection while maintaining the LLM outline."""
    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
    from worker.pipeline.wiki_planner import generate_wiki_plan

    outline = {
        "pages": [
            {"title": "Worker Pipeline", "purpose": "Pipeline stages."},
            {"title": "API Layer", "purpose": "REST and WebSocket endpoints."},
            {
                "title": "Pipeline Stages",
                "purpose": "Individual stages.",
                "parent": "Worker Pipeline",
            },
            {"title": "Endpoints", "purpose": "HTTP routes.", "parent": "API Layer"},
        ]
    }
    all_files = [
        "worker/pipeline/wiki_planner.py",
        "worker/pipeline/page_generator.py",
        "worker/pipeline/ast_analysis.py",
        "api/routers/repos.py",
        "api/routers/wiki.py",
    ]
    file_analysis = FileAnalysis(
        files={
            f: FileInfo(
                rel_path=f, entities=[], class_count=0, function_count=0, summary=""
            )
            for f in all_files
        }
    )

    # mock_llm.generate_structured will be called for Phase 1 (Outline)
    # and Phase 2 (Assignment). We make Phase 2 fail.
    # The first call (Outline) succeeds. Subsequent calls (Assignment batches) fail.
    mock_llm.generate_structured.side_effect = [
        outline,
        ValueError("Phase 2 always fails"),
        ValueError("Phase 2 always fails"),
    ]

    plan = await generate_wiki_plan(
        file_analysis,
        repo_name="testrepo",
        llm=mock_llm,
        max_retries=1,  # 1 retry + 1 initial = 2 attempts
    )

    # The outline from Phase 1 is preserved
    titles = [p.title for p in plan.pages]
    assert "Worker Pipeline" in titles
    assert "API Layer" in titles

    # But files are assigned via the score-based heuristic fallback.
    pipeline_page = next(p for p in plan.pages if p.title == "Worker Pipeline")
    api_page = next(p for p in plan.pages if p.title == "API Layer")

    assert all(
        f in pipeline_page.files for f in all_files if f.startswith("worker/pipeline/")
    )
    assert all(f in api_page.files for f in all_files if f.startswith("api/"))


async def test_assign_files_uses_batched_path(monkeypatch):
    """_select_files must delegate to _select_files_in_batches."""
    from unittest.mock import AsyncMock

    from worker.pipeline import wiki_planner as wp

    called = {}

    async def fake_batched(**kwargs):
        called["hit"] = True
        titles = [page["title"] for page in kwargs["outline"]]
        return {titles[0]: ["a.py"], titles[1]: ["b.py"]}

    monkeypatch.setattr(wp, "_select_files_in_batches", fake_batched)

    llm = AsyncMock()
    outline = [
        {"title": "One", "purpose": "p1"},
        {"title": "Two", "purpose": "p2"},
    ]
    result = await wp._select_files(
        outline=outline,
        file_summary="fs",
        dep_info=None,
        all_files=["a.py", "b.py"],
        file_infos={},
        dep_graph=None,
        llm=llm,
        system="sys",
        on_retry=None,
    )

    assert called.get("hit") is True
    assert "a.py" in result["One"]
    assert "b.py" in result["Two"]


async def test_select_files_preserves_partial_batches_on_raw_validation_failure(
    monkeypatch,
):
    """Valid earlier batches should survive a later relevance/schema failure."""
    from unittest.mock import AsyncMock

    from worker.pipeline import wiki_planner as wp

    monkeypatch.setattr(wp, "_PAGE_BATCH_SIZE", 1)
    monkeypatch.setattr(
        wp,
        "_prefilter_candidates",
        lambda _page, all_files, _file_infos, _dep_graph: list(all_files),
    )

    llm = AsyncMock()
    llm.generate_structured.side_effect = [
        {
            "selections": [
                {
                    "page_title": "Core",
                    "files": [_selected("core.py", 9)],
                }
            ]
        },
        {
            "selections": [
                {
                    "page_title": "API",
                    "files": [
                        {"path": "api.py", "relevance": "high"},
                    ],
                }
            ]
        },
    ]
    outline = [
        {"title": "Core", "purpose": "Core subsystem."},
        {"title": "API", "purpose": "API subsystem."},
    ]

    with pytest.raises(ValueError) as exc_info:
        await wp._select_files(
            outline=outline,
            file_summary="fs",
            dep_info=None,
            all_files=["core.py", "api.py", "routes.py"],
            file_infos={},
            dep_graph=None,
            llm=llm,
            system="sys",
            on_retry=None,
            max_retries=1,
        )

    assert isinstance(exc_info.value, wp._SelectionFailure)
    assert exc_info.value.last_error is not None
    assert exc_info.value.partial_result == {"Core": ["core.py"], "API": []}


async def test_select_files_retries_then_demotes_ordering_violations(
    monkeypatch,
    caplog,
):
    """Ordering-only failures should be salvaged after one feedback retry."""
    import logging
    from unittest.mock import AsyncMock

    from worker.pipeline import wiki_planner as wp

    monkeypatch.setattr(
        wp,
        "_prefilter_candidates",
        lambda _page, all_files, _file_infos, _dep_graph: list(all_files),
    )

    low_first = {
        "selections": [
            {
                "page_title": "API",
                "files": [
                    _selected("api/legacy.py", 2),
                    _selected("api/routes.py", 9),
                    _selected("api/models.py", 7),
                ],
            }
        ]
    }
    llm = AsyncMock()
    llm.generate_structured.side_effect = [low_first, low_first]
    outline = [{"title": "API", "purpose": "REST API."}]

    with caplog.at_level(logging.WARNING, logger="worker.planner"):
        result = await wp._select_files(
            outline=outline,
            file_summary="fs",
            dep_info=None,
            all_files=["api/legacy.py", "api/routes.py", "api/models.py"],
            file_infos={},
            dep_graph=None,
            llm=llm,
            system="sys",
            on_retry=None,
            max_retries=1,
        )

    assert result == {"API": ["api/routes.py", "api/models.py", "api/legacy.py"]}
    assert all(isinstance(path, str) for path in result["API"])
    assert llm.generate_structured.await_count == 2
    assert any(
        "wiki_planner.ordering_demotion" in record.getMessage()
        and "demoted_file=api/legacy.py" in record.getMessage()
        and "original_position=0" in record.getMessage()
        and "score=2" in record.getMessage()
        for record in caplog.records
    )


async def test_select_files_repeated_below_floor_only_ordering_is_salvaged(
    monkeypatch,
    caplog,
):
    """Repeated below-floor-only ordering failures return after one feedback retry."""
    import logging
    from unittest.mock import AsyncMock

    from worker.pipeline import wiki_planner as wp

    monkeypatch.setattr(
        wp,
        "_prefilter_candidates",
        lambda _page, all_files, _file_infos, _dep_graph: list(all_files),
    )

    below_floor = {
        "selections": [
            {
                "page_title": "API",
                "files": [_selected("api/legacy.py", 2)],
            }
        ]
    }
    llm = AsyncMock()
    llm.generate_structured.side_effect = [below_floor, below_floor]
    outline = [{"title": "API", "purpose": "REST API."}]

    with caplog.at_level(logging.WARNING, logger="worker.planner"):
        result = await wp._select_files(
            outline=outline,
            file_summary="fs",
            dep_info=None,
            all_files=["api/legacy.py"],
            file_infos={},
            dep_graph=None,
            llm=llm,
            system="sys",
            on_retry=None,
            max_retries=1,
        )

    assert result == {"API": ["api/legacy.py"]}
    assert isinstance(result, dict)
    assert all(isinstance(paths, list) for paths in result.values())
    assert llm.generate_structured.await_count == 2
    assert any(
        "wiki_planner.ordering_demotion" in record.getMessage()
        and "demoted_file=api/legacy.py" in record.getMessage()
        and "original_position=0" in record.getMessage()
        and "score=2" in record.getMessage()
        for record in caplog.records
    )


async def test_select_files_mixed_provider_and_ordering_failure_is_not_salvaged(
    monkeypatch,
):
    """Provider failure in one batch must block ordering-only salvage."""
    from unittest.mock import AsyncMock

    from worker.pipeline import wiki_planner as wp

    monkeypatch.setattr(wp, "_PAGE_BATCH_SIZE", 1)
    monkeypatch.setattr(
        wp,
        "_prefilter_candidates",
        lambda _page, all_files, _file_infos, _dep_graph: list(all_files),
    )

    async def fake_generate_structured(prompt, **_kwargs):
        text = prompt[0].text
        if 'Page: "Core"' in text:
            return {
                "selections": [
                    {"page_title": "Core", "files": [_selected("core.py", 9)]}
                ]
            }
        if 'Page: "Overview"' in text:
            raise RuntimeError("provider exhausted")
        return {
            "selections": [
                {
                    "page_title": "API",
                    "files": [
                        _selected("api/legacy.py", 2),
                        _selected("api/routes.py", 9),
                    ],
                }
            ]
        }

    llm = AsyncMock()
    llm.generate_structured.side_effect = fake_generate_structured
    outline = [
        {"title": "Core", "purpose": "Core subsystem."},
        {"title": "Overview", "purpose": "Repository overview."},
        {"title": "API", "purpose": "REST API."},
    ]

    with pytest.raises(ValueError) as exc_info:
        await wp._select_files(
            outline=outline,
            file_summary="fs",
            dep_info=None,
            all_files=["core.py", "api/legacy.py", "api/routes.py"],
            file_infos={},
            dep_graph=None,
            llm=llm,
            system="sys",
            on_retry=None,
            max_retries=1,
        )

    assert isinstance(exc_info.value, wp._SelectionFailure)
    assert exc_info.value.last_error is not None
    assert "provider exhausted" in exc_info.value.last_error
    assert exc_info.value.partial_result == {
        "Core": ["core.py"],
        "Overview": [],
        "API": [],
    }


async def test_select_files_provider_failure_preserves_successful_batch(
    monkeypatch,
):
    """Provider errors in a parallel batch must not be masked as empty output."""
    from unittest.mock import AsyncMock

    from worker.pipeline import wiki_planner as wp

    monkeypatch.setattr(wp, "_PAGE_BATCH_SIZE", 1)
    monkeypatch.setattr(
        wp,
        "_prefilter_candidates",
        lambda _page, all_files, _file_infos, _dep_graph: list(all_files),
    )

    async def fake_generate_structured(prompt, **_kwargs):
        text = prompt[0].text
        if 'Page: "Core"' in text:
            return {
                "selections": [
                    {"page_title": "Core", "files": [_selected("core.py", 9)]}
                ]
            }
        if 'Page: "API"' in text:
            raise RuntimeError("provider exhausted")
        return {
            "selections": [
                {"page_title": "Worker", "files": [_selected("worker.py", 9)]}
            ]
        }

    llm = AsyncMock()
    llm.generate_structured.side_effect = fake_generate_structured
    outline = [
        {"title": "Core", "purpose": "Core subsystem."},
        {"title": "API", "purpose": "API subsystem."},
        {"title": "Worker", "purpose": "Worker subsystem."},
    ]

    with pytest.raises(ValueError) as exc_info:
        await wp._select_files(
            outline=outline,
            file_summary="fs",
            dep_info=None,
            all_files=["core.py", "api.py", "worker.py"],
            file_infos={},
            dep_graph=None,
            llm=llm,
            system="sys",
            on_retry=None,
            max_retries=1,
        )

    assert isinstance(exc_info.value, wp._SelectionFailure)
    assert exc_info.value.last_error is not None
    assert "provider exhausted" in exc_info.value.last_error
    assert exc_info.value.partial_result == {
        "Core": ["core.py"],
        "API": [],
        "Worker": ["worker.py"],
    }


async def test_select_files_drains_parallel_batches_before_partial_failure(
    monkeypatch,
):
    """Successful sibling batches must be captured before partial fallback."""
    from unittest.mock import AsyncMock

    from worker.pipeline import wiki_planner as wp

    monkeypatch.setattr(wp, "_PAGE_BATCH_SIZE", 1)
    monkeypatch.setattr(
        wp,
        "_prefilter_candidates",
        lambda _page, all_files, _file_infos, _dep_graph: list(all_files),
    )

    async def fake_generate_structured(prompt, **_kwargs):
        text = prompt[0].text
        if 'Page: "Core"' in text:
            return {
                "selections": [
                    {"page_title": "Core", "files": [_selected("core.py", 9)]}
                ]
            }
        if 'Page: "API"' in text:
            return {
                "selections": [
                    {
                        "page_title": "API",
                        "files": [
                            {"path": "api.py", "relevance": "high"},
                        ],
                    }
                ]
            }
        return {
            "selections": [
                {"page_title": "Worker", "files": [_selected("worker.py", 9)]}
            ]
        }

    llm = AsyncMock()
    llm.generate_structured.side_effect = fake_generate_structured
    outline = [
        {"title": "Core", "purpose": "Core subsystem."},
        {"title": "API", "purpose": "API subsystem."},
        {"title": "Worker", "purpose": "Worker subsystem."},
    ]

    with pytest.raises(ValueError) as exc_info:
        await wp._select_files(
            outline=outline,
            file_summary="fs",
            dep_info=None,
            all_files=["core.py", "api.py", "routes.py", "worker.py"],
            file_infos={},
            dep_graph=None,
            llm=llm,
            system="sys",
            on_retry=None,
            max_retries=1,
        )

    assert isinstance(exc_info.value, wp._SelectionFailure)
    assert exc_info.value.last_error is not None
    assert exc_info.value.partial_result == {
        "Core": ["core.py"],
        "API": [],
        "Worker": ["worker.py"],
    }


def test_build_outline_prompt_includes_anchors_section_when_provided():
    """When anchors are passed in, the prompt must surface them under a
    dedicated heading, not bury them in the existing sections."""
    from worker.pipeline.wiki_planner import _build_outline_prompt

    prompt = _build_outline_prompt(
        file_summary="one.py, two.py",
        repo_name="demo",
        anchors_block=(
            "## Directory layout\nworker/ (3)\n"
            "\n## Package docstrings\nworker: core pipeline."
        ),
    )
    assert "Architectural anchors" in prompt
    assert "worker/ (3)" in prompt
    assert "worker: core pipeline." in prompt
    # Still contains the existing guidance
    assert "Create a hierarchical wiki plan." in prompt


def test_build_outline_prompt_without_anchors_unchanged():
    """Call sites that do not pass anchors must not see an anchors section."""
    from worker.pipeline.wiki_planner import _build_outline_prompt

    prompt = _build_outline_prompt(
        file_summary="one.py, two.py",
        repo_name="demo",
    )
    assert "Architectural anchors" not in prompt


def test_build_outline_prompt_requires_en_keywords_for_cjk_with_examples():
    from worker.pipeline.wiki_planner import _build_outline_prompt

    prompt = _build_outline_prompt(
        file_summary="web/components/Sidebar.tsx\napi/routers/repos.py",
        repo_name="demo",
    )

    assert (
        "en_keywords REQUIRED when title or purpose contains non-Latin/CJK characters"
    ) in prompt
    assert "optional otherwise" in prompt
    assert (
        "list 3-8 English keywords drawn from directory names, module names, "
        "or file basenames"
    ) in prompt
    assert '["web", "components", "next"]' in prompt
    assert '["api", "routers", "fastapi"]' in prompt


def test_to_internal_json_roundtrips_files():
    plan = WikiPlan(pages=[WikiPageSpec(title="Core", purpose="p", files=["a.py"])])
    payload = plan.to_internal_json()
    page = payload["pages"][0]
    assert page["files"] == ["a.py"]
    assert "secondary_files" not in page


def test_to_wiki_json_omits_files():
    """wiki.json is user-facing: file assignments must not appear."""
    plan = WikiPlan(pages=[WikiPageSpec(title="Core", purpose="p", files=["a.py"])])
    payload = plan.to_wiki_json()
    page = payload["pages"][0]
    assert "files" not in page


def test_to_api_structure_no_secondary_file_count():
    plan = WikiPlan(pages=[WikiPageSpec(title="Core", purpose="p", files=["a.py"])])
    page = plan.to_api_structure()["pages"][0]
    assert "secondary_file_count" not in page
    assert "secondary_files" not in page


async def test_generate_wiki_plan_with_clone_root(mock_llm):
    """generate_wiki_plan exercises the clone_root anchors-building branch."""
    from pathlib import Path

    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo

    fixture_root = Path(__file__).parent.parent / "fixtures" / "simple-repo"
    files = ["main.py", "models.py", "utils.py"]
    file_analysis = FileAnalysis(
        files={f: FileInfo(rel_path=f, entities=[], summary="") for f in files}
    )
    mock_llm.generate_structured.side_effect = [
        {
            "pages": [
                {"title": "Core", "purpose": "Core subsystem."},
                {"title": "Infra", "purpose": "Infrastructure."},
                {"title": "Models", "purpose": "Data models.", "parent": "Core"},
                {"title": "Utils", "purpose": "Utility helpers.", "parent": "Infra"},
            ]
        },
        {
            "selections": [
                {
                    "page_title": "Models",
                    "files": [_selected("main.py", 8), _selected("models.py", 7)],
                },
                {"page_title": "Utils", "files": [_selected("utils.py", 8)]},
            ]
        },
    ]

    plan = await generate_wiki_plan(
        file_analysis,
        repo_name="simple-repo",
        llm=mock_llm,
        clone_root=fixture_root,
    )

    assert len(plan.pages) == 4
    titles = {p.title for p in plan.pages}
    assert titles == {"Core", "Infra", "Models", "Utils"}
    # The outline prompt received by Phase 1 should have included an anchors block;
    # verify the plan was produced (anchors don't appear in plan output, but the
    # call must complete without error and produce a valid plan).
    assert any(p.files for p in plan.pages)


# ---------------------------------------------------------------------------
# _score_file_for_page and _prefilter_candidates tests
# ---------------------------------------------------------------------------


class FakeFileInfo:
    def __init__(self, entities):
        self.entities = entities


def _fake_infos(*paths_entities):
    return {path: FakeFileInfo(ents) for path, ents in paths_entities}


def test_validate_outline_rejects_cjk_title_without_en_keywords():
    pages = [
        {"title": "前端应用", "purpose": "介绍 Next.js 应用架构。"},
        {"title": "Backend", "purpose": "Backend subsystem."},
        {"title": "Components", "purpose": "组件库。", "parent": "前端应用"},
    ]

    with pytest.raises(ValueError, match="en_keywords"):
        _validate_outline_structure(pages, page_range=(1, 10), total_file_count=10)


def test_validate_outline_rejects_cjk_compatibility_ideograph_without_en_keywords():
    pages = [
        {"title": "\ufa11 UI", "purpose": "Compatibility ideograph page."},
        {"title": "Backend", "purpose": "Backend subsystem."},
    ]

    with pytest.raises(ValueError, match="en_keywords"):
        _validate_outline_structure(pages, page_range=(1, 10), total_file_count=10)


def test_validate_outline_accepts_ascii_title_without_en_keywords():
    pages = [
        {"title": "Frontend App", "purpose": "Describes the Next.js app."},
        {"title": "Backend", "purpose": "Backend subsystem."},
        {"title": "Components", "purpose": "UI components.", "parent": "Frontend App"},
    ]

    _validate_outline_structure(pages, page_range=(1, 10), total_file_count=10)


def test_validate_outline_accepts_cjk_page_with_en_keywords():
    pages = [
        {
            "title": "前端应用",
            "purpose": "介绍 Next.js 应用架构。",
            "en_keywords": ["web", "app", "next"],
        },
        {"title": "Backend", "purpose": "Backend subsystem."},
        {
            "title": "Components",
            "purpose": "组件库。",
            "parent": "前端应用",
            "en_keywords": ["web", "components", "next"],
        },
    ]

    _validate_outline_structure(pages, page_range=(1, 10), total_file_count=10)


def test_score_prefers_code_over_doc():
    page = {"title": "API Gateway", "purpose": "Handles HTTP routing."}
    infos = _fake_infos(("api/routes.py", ["route_a", "route_b"]), ("docs/api.md", []))
    code_score = _score_file_for_page("api/routes.py", page, infos, None)
    doc_score = _score_file_for_page("docs/api.md", page, infos, None)
    assert code_score > doc_score


def test_score_entity_density():
    page = {"title": "Worker", "purpose": "Background jobs."}
    sparse = _fake_infos(("worker/job.py", ["run"]))
    dense = _fake_infos(("worker/job.py", [f"fn{i}" for i in range(15)]))
    assert _score_file_for_page(
        "worker/job.py", page, dense, None
    ) > _score_file_for_page("worker/job.py", page, sparse, None)


def test_score_semantic_alignment():
    page = {"title": "API Gateway", "purpose": "Routes requests."}
    infos = _fake_infos(("api/gateway.py", ["route"]), ("util/helper.py", ["route"]))
    assert _score_file_for_page(
        "api/gateway.py", page, infos, None
    ) > _score_file_for_page("util/helper.py", page, infos, None)


def test_score_semantic_alignment_uses_unicode_tokens():
    page = {"title": "认证 服务", "purpose": "处理 用户 登录"}
    infos = _fake_infos(("services/认证/登录.py", []), ("services/misc.py", []))

    assert _score_file_for_page(
        "services/认证/登录.py", page, infos, None
    ) > _score_file_for_page("services/misc.py", page, infos, None)


def test_score_en_keywords_path_overlap_boosts_cjk_page():
    page = {
        "title": "前端应用架构",
        "purpose": "介绍交互组件库。",
        "en_keywords": ["web", "components", "next"],
    }
    infos = _fake_infos(
        ("web/components/Sidebar.tsx", []),
        ("server/jobs/Worker.ts", []),
    )

    score_match = _score_file_for_page("web/components/Sidebar.tsx", page, infos, None)
    score_miss = _score_file_for_page("server/jobs/Worker.ts", page, infos, None)

    assert score_match >= score_miss + 4


def test_score_en_keywords_exact_boost_handles_windows_separators():
    page = {
        "title": "前端应用架构",
        "purpose": "介绍交互组件库。",
        "en_keywords": ["components", "frontend", "sidebar"],
    }
    infos = _fake_infos(
        ("web\\components\\Sidebar.tsx", []),
        ("server/jobs/Worker.tsx", []),
    )

    score_match = _score_file_for_page(
        "web\\components\\Sidebar.tsx", page, infos, None
    )
    score_miss = _score_file_for_page("server/jobs/Worker.tsx", page, infos, None)

    assert score_match >= score_miss + 8


def test_prefilter_preserves_two_character_architectural_signals():
    page = {"title": "UI Components", "purpose": "Frontend UI controls."}
    all_files = ["server/payments/Invoice.ts", "web/ui/Button.tsx"]
    infos = {
        "web/ui/Button.tsx": FakeFileInfo([]),
        "server/payments/Invoice.ts": FakeFileInfo([]),
    }

    result = _prefilter_candidates(page, all_files, infos, None)

    assert result[0] == "web/ui/Button.tsx"


def test_prefilter_returns_at_most_max_candidates():
    page = {"title": "Worker", "purpose": "Background jobs."}
    all_files = [f"worker/file{i}.py" for i in range(50)]
    infos = {f: FakeFileInfo([f"fn{i}"]) for i, f in enumerate(all_files)}
    result = _prefilter_candidates(page, all_files, infos, None, max_candidates=10)
    assert len(result) <= 10


def test_prefilter_default_handles_moderately_large_candidate_sets():
    page = {"title": "Worker", "purpose": "Background jobs."}
    all_files = [f"worker/file{i}.py" for i in range(30)]
    infos = {f: FakeFileInfo([f"fn{i}"]) for i, f in enumerate(all_files)}
    result = _prefilter_candidates(page, all_files, infos, None)
    assert set(result) == set(all_files)


def test_prefilter_prefers_code_files():
    page = {"title": "Auth", "purpose": "Authentication logic."}
    all_files = ["auth/login.py", "auth/README.md", "auth/config.yaml"]
    infos = {
        "auth/login.py": FakeFileInfo(["authenticate"]),
        "auth/README.md": FakeFileInfo([]),
        "auth/config.yaml": FakeFileInfo([]),
    }
    result = _prefilter_candidates(page, all_files, infos, None)
    assert result[0] == "auth/login.py"


class FakeFileInfoH:
    def __init__(self, entities):
        self.entities = entities


def test_heuristic_select_files_picks_code_over_docs():
    outline = [{"title": "Auth", "purpose": "Authentication logic."}]
    all_files = ["auth/login.py", "auth/README.md", "auth/config.yaml"]
    infos = {
        "auth/login.py": FakeFileInfoH(["authenticate", "logout"]),
        "auth/README.md": FakeFileInfoH([]),
        "auth/config.yaml": FakeFileInfoH([]),
    }
    result = _heuristic_select_files(outline, all_files, infos, None)
    assert "auth/login.py" in result["Auth"]
    assert "auth/README.md" not in result["Auth"]


def test_heuristic_select_files_uses_partial_llm_selections():
    outline = [
        {"title": "API", "purpose": "REST endpoints."},
        {"title": "DB", "purpose": "Database models."},
    ]
    all_files = ["api/routes.py", "db/models.py"]
    infos = {
        "api/routes.py": FakeFileInfoH(["get", "post"]),
        "db/models.py": FakeFileInfoH(["User", "Session"]),
    }
    partial = {"API": ["api/routes.py"]}
    result = _heuristic_select_files(outline, all_files, infos, None, partial)
    assert result["API"] == ["api/routes.py"]
    assert "db/models.py" in result["DB"]


def test_heuristic_select_files_respects_max():
    outline = [{"title": "Core", "purpose": "Core logic."}]
    all_files = [f"core/module{i}.py" for i in range(30)]
    infos = {f: FakeFileInfoH([f"fn{i}"]) for i, f in enumerate(all_files)}
    result = _heuristic_select_files(outline, all_files, infos, None)
    assert len(result["Core"]) <= 10


# ---------------------------------------------------------------------------
# _validate_selections tests
# ---------------------------------------------------------------------------


def test_validate_raw_selections_rejects_increasing_relevance_scores():
    raw = {
        "selections": [
            {
                "page_title": "API",
                "files": [
                    {"path": "api/routes.py", "relevance": 6},
                    {"path": "api/models.py", "relevance": 7},
                ],
            }
        ]
    }

    with pytest.raises(wp._SelectionOrderingError, match="non-increasing"):
        wp._validate_raw_selections(
            raw,
            valid_titles={"API"},
            all_files_set={"api/routes.py", "api/models.py"},
            candidate_files_by_title={"API": {"api/routes.py", "api/models.py"}},
        )


def test_validate_raw_selections_rejects_first_relevance_below_floor():
    raw = {
        "selections": [
            {
                "page_title": "API",
                "files": [{"path": "api/routes.py", "relevance": 2}],
            }
        ]
    }

    with pytest.raises(wp._SelectionOrderingError, match="first file"):
        wp._validate_raw_selections(
            raw,
            valid_titles={"API"},
            all_files_set={"api/routes.py"},
            candidate_files_by_title={"API": {"api/routes.py"}},
        )


def test_validate_raw_selections_applies_first_relevance_floor_after_filtering():
    raw = {
        "selections": [
            {
                "page_title": "API",
                "files": [
                    {"path": "docs/api.md", "relevance": 9},
                    {"path": "api/routes.py", "relevance": 2},
                ],
            }
        ]
    }

    with pytest.raises(wp._SelectionOrderingError, match="first file"):
        wp._validate_raw_selections(
            raw,
            valid_titles={"API"},
            all_files_set={"docs/api.md", "api/routes.py"},
            candidate_files_by_title={"API": {"api/routes.py"}},
        )


def test_validate_raw_selections_unwraps_and_filters_candidate_paths():
    raw = {
        "selections": [
            {
                "page_title": "API",
                "files": [
                    {"path": "api/routes.py", "relevance": 9},
                    {"path": "misc/unused.py", "relevance": 8},
                ],
            }
        ]
    }

    result = wp._validate_raw_selections(
        raw,
        valid_titles={"API"},
        all_files_set={"api/routes.py", "misc/unused.py"},
        candidate_files_by_title={"API": {"api/routes.py"}},
    )

    assert result == {"API": ["api/routes.py"]}


def test_demote_ordering_invalid_raw_selection_salvages_below_floor_only_file(
    caplog,
):
    import logging

    raw = {
        "selections": [
            {
                "page_title": "API",
                "files": [
                    {"path": "docs/api.md", "relevance": 9},
                    {"path": "api/legacy.py", "relevance": 2},
                ],
            }
        ]
    }
    error = wp._SelectionOrderingError(
        "VALIDATION_FAILURE: Page 'API' first file relevance must be >= 3",
        raw=raw,
        valid_titles={"API"},
        all_files_set={"docs/api.md", "api/legacy.py"},
        candidate_files_by_title={"API": {"api/legacy.py"}},
        reason="first file relevance below 3",
    )

    with caplog.at_level(logging.WARNING, logger="worker.planner"):
        result = wp._demote_ordering_invalid_raw_selection(error)

    assert result == {"API": ["api/legacy.py"]}
    assert any(
        "wiki_planner.ordering_demotion" in record.getMessage()
        and "demoted_file=api/legacy.py" in record.getMessage()
        and "original_position=0" in record.getMessage()
        and "score=2" in record.getMessage()
        for record in caplog.records
    )


def test_validate_selections_passes_normal():
    outline = [{"title": "API", "purpose": "REST API."}]
    result = {"API": ["api/routes.py", "api/models.py"]}
    _validate_selections(result, outline)  # should not raise


def test_validate_selections_fails_over_max():
    outline = [{"title": "API", "purpose": "REST API."}]
    result = {"API": [f"api/file{i}.py" for i in range(11)]}
    with pytest.raises(ValueError, match="VALIDATION_FAILURE"):
        _validate_selections(result, outline)


def test_validate_selections_fails_empty_leaf_page():
    outline = [{"title": "Auth", "purpose": "Login logic."}]
    result = {"Auth": []}
    with pytest.raises(ValueError, match="VALIDATION_FAILURE"):
        _validate_selections(result, outline)


def test_validate_selections_allows_empty_parent():
    outline = [
        {"title": "Backend", "purpose": "Parent."},
        {"title": "Auth", "purpose": "Login.", "parent": "Backend"},
    ]
    result = {"Backend": [], "Auth": ["auth/login.py"]}
    _validate_selections(result, outline)  # parent with no files is fine


def test_enforce_ownership_demotes_lower_scoring_sibling_duplicate():
    outline = [
        {"title": "Backend", "purpose": "Parent."},
        {"title": "API Routes", "purpose": "REST API routes.", "parent": "Backend"},
        {"title": "Worker Jobs", "purpose": "Background jobs.", "parent": "Backend"},
    ]
    selections = {
        "Backend": [],
        "API Routes": ["api/routes.py"],
        "Worker Jobs": ["api/routes.py", "worker/jobs.py"],
    }

    result = _enforce_ownership(
        selections,
        outline,
        all_repo_files=["api/routes.py", "worker/jobs.py"],
        file_infos={},
        dep_graph=None,
        mode="enforce",
    )

    assert result["API Routes"] == ["api/routes.py"]
    assert result["Worker Jobs"] == ["worker/jobs.py"]
    assert sum(paths.count("api/routes.py") for paths in result.values()) == 1


def test_enforce_ownership_allows_two_non_sibling_owners_for_non_hub_file():
    outline = [
        {"title": "Backend", "purpose": "Backend parent."},
        {"title": "Runtime", "purpose": "Runtime parent."},
        {"title": "API", "purpose": "REST API.", "parent": "Backend"},
        {"title": "Worker", "purpose": "Background jobs.", "parent": "Runtime"},
    ]
    selections = {
        "Backend": [],
        "Runtime": [],
        "API": ["shared/core.py"],
        "Worker": ["shared/core.py"],
    }

    result = _enforce_ownership(
        selections,
        outline,
        all_repo_files=["shared/core.py"],
        file_infos={},
        dep_graph=None,
        mode="enforce",
    )

    assert result == selections


def test_enforce_ownership_caps_three_non_sibling_owners_for_non_hub_file():
    outline = [
        {"title": "Backend", "purpose": "Backend parent."},
        {"title": "Runtime", "purpose": "Runtime parent."},
        {"title": "Data", "purpose": "Data parent."},
        {"title": "API Routes", "purpose": "REST API routes.", "parent": "Backend"},
        {"title": "Route Models", "purpose": "Route models.", "parent": "Runtime"},
        {"title": "Worker Jobs", "purpose": "Background jobs.", "parent": "Data"},
    ]
    selections = {
        "Backend": [],
        "Runtime": [],
        "Data": [],
        "API Routes": ["api/routes.py"],
        "Route Models": ["api/routes.py"],
        "Worker Jobs": ["api/routes.py", "worker/jobs.py"],
    }

    result = _enforce_ownership(
        selections,
        outline,
        all_repo_files=["api/routes.py", "worker/jobs.py"],
        file_infos={},
        dep_graph=None,
        mode="enforce",
    )

    owners = [title for title, paths in result.items() if "api/routes.py" in paths]
    assert owners == ["API Routes", "Route Models"]
    assert result["Worker Jobs"] == ["worker/jobs.py"]


def test_enforce_ownership_off_leaves_duplicates_untouched():
    outline = [
        {"title": "Backend", "purpose": "Parent."},
        {"title": "API Routes", "purpose": "REST API routes.", "parent": "Backend"},
        {"title": "Worker Jobs", "purpose": "Background jobs.", "parent": "Backend"},
    ]
    selections = {
        "Backend": [],
        "API Routes": ["api/routes.py"],
        "Worker Jobs": ["api/routes.py", "worker/jobs.py"],
    }

    result = _enforce_ownership(
        selections,
        outline,
        all_repo_files=["api/routes.py", "worker/jobs.py"],
        file_infos={},
        dep_graph=None,
        mode="off",
    )

    assert result == selections


def test_enforce_ownership_exempts_top_decile_in_degree_hub_files():
    class FakeDepGraph:
        edges = {
            "a.py": ["shared/hub.py"],
            "b.py": ["shared/hub.py"],
            "c.py": ["shared/hub.py"],
            "shared/hub.py": [],
        }

    outline = [
        {"title": "Backend", "purpose": "Parent."},
        {"title": "API Routes", "purpose": "REST API routes.", "parent": "Backend"},
        {"title": "Worker Jobs", "purpose": "Background jobs.", "parent": "Backend"},
    ]
    selections = {
        "Backend": [],
        "API Routes": ["shared/hub.py"],
        "Worker Jobs": ["shared/hub.py", "worker/jobs.py"],
    }

    result = _enforce_ownership(
        selections,
        outline,
        all_repo_files=["shared/hub.py", "worker/jobs.py"],
        file_infos={},
        dep_graph=FakeDepGraph(),
        mode="enforce",
    )

    assert result == selections


def test_enforce_ownership_caps_total_assignments():
    outline = [
        {"title": "API Routes", "purpose": "REST API routes."},
        {"title": "Worker Jobs", "purpose": "Background jobs."},
        {"title": "Database Models", "purpose": "Database models."},
    ]
    all_files = ["api/routes.py", "worker/jobs.py", "db/models.py", "shared/util.py"]
    selections = {
        "API Routes": ["api/routes.py", "shared/util.py", "worker/jobs.py"],
        "Worker Jobs": ["worker/jobs.py", "shared/util.py", "api/routes.py"],
        "Database Models": ["db/models.py", "shared/util.py", "api/routes.py"],
    }

    result = _enforce_ownership(
        selections,
        outline,
        all_repo_files=all_files,
        file_infos={},
        dep_graph=None,
        mode="enforce",
    )

    assert sum(len(paths) for paths in result.values()) <= int(1.5 * len(all_files))
    assert result["API Routes"]
    assert result["Worker Jobs"]
    assert result["Database Models"]


async def test_select_files_enforces_ownership_after_validation(
    monkeypatch,
    mock_llm,
):
    from worker.pipeline import wiki_planner as wp

    monkeypatch.setattr(
        wp,
        "_prefilter_candidates",
        lambda _page, all_files, _file_infos, _dep_graph: list(all_files),
    )
    mock_llm.generate_structured.side_effect = None
    mock_llm.generate_structured.return_value = {
        "selections": [
            {"page_title": "Backend", "files": []},
            {"page_title": "API Routes", "files": [_selected("api/routes.py", 9)]},
            {
                "page_title": "Worker Jobs",
                "files": [
                    _selected("api/routes.py", 8),
                    _selected("worker/jobs.py", 7),
                ],
            },
        ]
    }
    outline = [
        {"title": "Backend", "purpose": "Parent."},
        {"title": "API Routes", "purpose": "REST API routes.", "parent": "Backend"},
        {"title": "Worker Jobs", "purpose": "Background jobs.", "parent": "Backend"},
    ]

    result = await wp._select_files(
        outline=outline,
        file_summary="files",
        dep_info=None,
        all_files=["api/routes.py", "worker/jobs.py"],
        file_infos={},
        dep_graph=None,
        llm=mock_llm,
        system="sys",
        on_retry=None,
        max_retries=1,
    )

    assert result["API Routes"] == ["api/routes.py"]
    assert result["Worker Jobs"] == ["worker/jobs.py"]


def test_validate_wiki_plan_no_orphan_check():
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top level.", "files": ["main.py"]},
        ]
    }
    plan = validate_wiki_plan(raw, all_files=["main.py", "worker/core.py"])
    assert plan.pages[0].title == "Overview"
    # worker/core.py is unassigned — no error raised


def test_wiki_plan_all_repo_files_roundtrip():
    plan = WikiPlan(
        pages=[WikiPageSpec(title="Overview", purpose="Top level.", files=["main.py"])],
        all_repo_files=["main.py", "worker/core.py", "tests/test_core.py"],
    )
    data = plan.to_internal_json()
    assert data["all_repo_files"] == ["main.py", "worker/core.py", "tests/test_core.py"]
    # wiki.json (user-facing) should NOT include all_repo_files
    wiki_data = plan.to_wiki_json()
    assert "all_repo_files" not in wiki_data


async def test_generate_wiki_plan_uses_selection_model(mock_llm):
    """Phase 2 should produce page-centric selections, not exhaustive assignments."""
    from unittest.mock import AsyncMock

    # Build a minimal FileAnalysis with 4 files
    file_analysis = FileAnalysis(
        files={
            "api/routes.py": FileInfo(
                rel_path="api/routes.py",
                entities=[
                    {"name": "get_user", "kind": "function", "line": 1},
                    {"name": "create_user", "kind": "function", "line": 10},
                ],
                summary="get_user, create_user",
            ),
            "api/models.py": FileInfo(
                rel_path="api/models.py",
                entities=[
                    {"name": "User", "kind": "function", "line": 1},
                    {"name": "Session", "kind": "function", "line": 20},
                ],
                summary="User, Session",
            ),
            "worker/job.py": FileInfo(
                rel_path="worker/job.py",
                entities=[{"name": "run_job", "kind": "function", "line": 1}],
                summary="run_job",
            ),
            "tests/test_api.py": FileInfo(
                rel_path="tests/test_api.py",
                entities=[{"name": "test_get_user", "kind": "function", "line": 1}],
                summary="test_get_user",
            ),
        }
    )

    # Phase 1: outline response — 2-level hierarchy with 2 subsystems
    outline_response = {
        "pages": [
            {"title": "API", "purpose": "REST endpoints."},
            {"title": "Backend", "purpose": "Background processing."},
            {"title": "Routes", "purpose": "HTTP route handlers.", "parent": "API"},
            {"title": "Worker", "purpose": "Background jobs.", "parent": "Backend"},
        ]
    }
    # Phase 2: selection response — tests/test_api.py intentionally omitted
    selection_response = {
        "selections": [
            {
                "page_title": "Routes",
                "files": [_selected("api/routes.py", 8), _selected("api/models.py", 7)],
            },
            {"page_title": "Worker", "files": [_selected("worker/job.py", 8)]},
        ]
    }
    mock_llm.generate_structured = AsyncMock(
        side_effect=[outline_response, selection_response]
    )

    plan = await generate_wiki_plan(
        file_analysis=file_analysis,
        repo_name="test-repo",
        llm=mock_llm,
    )

    assert isinstance(plan, WikiPlan)
    routes_page = next(p for p in plan.pages if p.title == "Routes")
    assert "api/routes.py" in routes_page.files
    assert "api/models.py" in routes_page.files
    worker_page = next(p for p in plan.pages if p.title == "Worker")
    assert "worker/job.py" in worker_page.files
    # tests/test_api.py is not selected — this is the key difference from the old model
    all_selected = {f for p in plan.pages for f in p.files}
    assert "tests/test_api.py" not in all_selected
    # all_repo_files should contain ALL analyzed files
    assert set(plan.all_repo_files) == set(file_analysis.files.keys())
