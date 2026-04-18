import pytest

from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
from worker.pipeline.wiki_planner import (
    WikiPageSpec,
    WikiPlan,
    _heuristic_select_files,
    _prefilter_candidates,
    _score_file_for_page,
    _suggest_page_range,
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
    """_select_files returns a dict mapping page titles to file lists."""
    from worker.pipeline.wiki_planner import _select_files

    mock_llm.generate_structured.side_effect = None
    mock_llm.generate_structured.return_value = {
        "selections": [
            {"page_title": "Overview", "files": ["main.py"]},
            {"page_title": "API", "files": ["api.py"]},
            {"page_title": "Worker", "files": ["worker.py"]},
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
            {"page_title": "Overview", "files": ["main.py"]},
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
    """Overview page with 0 files is allowed (orphans get assigned to it)."""
    raw = {
        "pages": [
            {"title": "Overview", "purpose": "Top.", "files": []},
            {"title": "API", "purpose": "Endpoints.", "files": ["api.py"]},
        ]
    }
    plan = validate_wiki_plan(raw)
    assert len(plan.pages) == 2


def test_validate_rejects_too_deep_hierarchy():
    raw = {
        "pages": [
            {"title": "L0", "purpose": ".", "files": ["a.py"]},
            {"title": "L1", "purpose": ".", "parent": "L0", "files": ["b.py"]},
            {"title": "L2", "purpose": ".", "parent": "L1", "files": ["c.py"]},
            {"title": "L3", "purpose": ".", "parent": "L2", "files": ["d.py"]},
            {"title": "L4", "purpose": ".", "parent": "L3", "files": ["e.py"]},
        ]
    }
    with pytest.raises(ValueError, match="flatten to at most 4 levels"):
        validate_wiki_plan(raw)


def test_validate_allows_4_level_hierarchy():
    """Hierarchy at exactly 4 levels deep should pass."""
    raw = {
        "pages": [
            {"title": "L0", "purpose": ".", "files": ["a.py"]},
            {"title": "L1", "purpose": ".", "parent": "L0", "files": ["b.py"]},
            {"title": "L2", "purpose": ".", "parent": "L1", "files": ["c.py"]},
            {"title": "L3", "purpose": ".", "parent": "L2", "files": ["d.py"]},
        ]
    }
    plan = validate_wiki_plan(raw)
    assert len(plan.pages) == 4


def test_validate_rejects_flat_plan_for_large_repo():
    # Use ≤10 files per page to avoid the "overloaded" check;
    # enough total files (35) to trigger the hierarchy check.
    page1_files = [f"f{i}.py" for i in range(10)]
    page2_files = [f"g{i}.py" for i in range(10)]
    raw = {
        "pages": [
            {"title": "Page1", "purpose": ".", "files": page1_files},
            {"title": "Page2", "purpose": ".", "files": page2_files},
        ]
    }
    all_files = [f"f{i}.py" for i in range(20)] + [f"g{i}.py" for i in range(15)]
    with pytest.raises(ValueError, match="create 2-3 levels of hierarchy"):
        validate_wiki_plan(raw, all_files=all_files)


def test_validate_rejects_too_few_pages():
    # Use ≤10 files per page to avoid the "overloaded" check;
    # supply enough pages to exercise the too-few-pages validation.
    raw = {
        "pages": [
            {
                "title": "Overview",
                "purpose": ".",
                "files": [f"f{i}.py" for i in range(5)],
            },
            {
                "title": "Core",
                "purpose": ".",
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
            {"title": "Overview", "purpose": "Top"},
            {"title": "Core", "purpose": "Core logic", "parent": "Overview"},
            {"title": "Utils", "purpose": "Helpers", "parent": "Overview"},
            {"title": "API", "purpose": "Routes", "parent": "Overview"},
            {"title": "Tests", "purpose": "Tests", "parent": "Overview"},
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
        # Phase 2: file selection
        {
            "selections": [
                {"page_title": "Overview", "files": ["main.py"]},
                {"page_title": "Models", "files": ["models.py"]},
                {"page_title": "Utilities", "files": ["utils.py"]},
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
    assert plan.pages[titles.index("Overview")].files == ["main.py"]
    assert plan.pages[titles.index("Models")].files == ["models.py"]
    assert plan.pages[titles.index("Utilities")].files == ["utils.py"]


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
    stuffed = {"selections": [{"page_title": "Overview", "files": ["main.py"]}]}
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
    using directory-clustering while maintaining the LLM outline."""
    from worker.pipeline.ast_analysis import FileAnalysis, FileInfo
    from worker.pipeline.wiki_planner import generate_wiki_plan

    outline = {
        "pages": [
            {"title": "Worker Pipeline", "purpose": "Pipeline stages."},
            {"title": "API Layer", "purpose": "REST and WebSocket endpoints."},
            {"title": "Overview", "purpose": "Main entrance."},
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

    # But files are assigned via heuristic (directory clustering)
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


# ── Stage B: secondary_files ──────────────────────────────────────────────


def test_wiki_page_spec_has_secondary_files():
    """WikiPageSpec must carry an optional secondary_files list."""
    spec = WikiPageSpec(
        title="Core",
        purpose="Core",
        files=["a.py"],
        secondary_files=["shared/utils.py"],
    )
    assert spec.files == ["a.py"]
    assert spec.secondary_files == ["shared/utils.py"]


def test_wiki_page_spec_secondary_files_default_empty():
    spec = WikiPageSpec(title="Core", purpose="p")
    assert spec.secondary_files == []


def test_to_internal_json_roundtrips_secondary_files():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Core",
                purpose="p",
                files=["a.py"],
                secondary_files=["b.py"],
            )
        ]
    )
    payload = plan.to_internal_json()
    page = payload["pages"][0]
    assert page["files"] == ["a.py"]
    assert page["secondary_files"] == ["b.py"]


def test_to_wiki_json_omits_secondary_files():
    """wiki.json is user-facing: secondary assignment must not appear."""
    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Core",
                purpose="p",
                files=["a.py"],
                secondary_files=["b.py"],
            )
        ]
    )
    payload = plan.to_wiki_json()
    page = payload["pages"][0]
    assert "files" not in page
    assert "secondary_files" not in page


def test_to_api_structure_exposes_secondary_file_count_only():
    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Core",
                purpose="p",
                files=["a.py"],
                secondary_files=["b.py", "c.py"],
            )
        ]
    )
    page = plan.to_api_structure()["pages"][0]
    assert page["secondary_file_count"] == 2
    assert "secondary_files" not in page


def test_validate_wiki_plan_considers_secondary_assignment_for_orphans():
    """A file that is secondary on some page still counts as assigned."""
    from worker.pipeline.wiki_planner import validate_wiki_plan

    raw = {
        "pages": [
            {
                "title": "Overview",
                "purpose": "top",
                "files": ["x.py"],
                "secondary_files": [],
            },
            {
                "title": "Core",
                "purpose": "core",
                "files": ["core.py"],
                "secondary_files": ["shared.py"],
            },
        ]
    }
    plan = validate_wiki_plan(raw, all_files=["x.py", "core.py", "shared.py"])
    # ``shared.py`` must NOT be appended to Overview as an orphan because
    # it's already referenced as secondary on Core.
    overview = next(p for p in plan.pages if p.title == "Overview")
    assert "shared.py" not in overview.files
    core = next(p for p in plan.pages if p.title == "Core")
    assert core.secondary_files == ["shared.py"]


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
                {"title": "Overview", "purpose": "Top-level overview."},
                {"title": "Models", "purpose": "Data models."},
                {"title": "Utils", "purpose": "Utility helpers."},
            ]
        },
        {
            "selections": [
                {"page_title": "Overview", "files": ["main.py"]},
                {"page_title": "Models", "files": ["models.py"]},
                {"page_title": "Utils", "files": ["utils.py"]},
            ]
        },
    ]

    plan = await generate_wiki_plan(
        file_analysis,
        repo_name="simple-repo",
        llm=mock_llm,
        clone_root=fixture_root,
    )

    assert len(plan.pages) == 3
    titles = {p.title for p in plan.pages}
    assert titles == {"Overview", "Models", "Utils"}
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


def test_prefilter_returns_at_most_max_candidates():
    page = {"title": "Worker", "purpose": "Background jobs."}
    all_files = [f"worker/file{i}.py" for i in range(50)]
    infos = {f: FakeFileInfo([f"fn{i}"]) for i, f in enumerate(all_files)}
    result = _prefilter_candidates(page, all_files, infos, None, max_candidates=10)
    assert len(result) <= 10


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
