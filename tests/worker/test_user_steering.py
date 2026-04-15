"""Tests for the `.autowiki/wiki.json` user-steering loader."""

from __future__ import annotations

import json


def test_load_returns_none_when_missing(tmp_path):
    from worker.pipeline.user_steering import load_user_steering

    assert load_user_steering(tmp_path) is None


def test_load_parses_full_schema(tmp_path):
    from worker.pipeline.user_steering import load_user_steering

    cfg_dir = tmp_path / ".autowiki"
    cfg_dir.mkdir()
    (cfg_dir / "wiki.json").write_text(
        json.dumps(
            {
                "repo_notes": ["Treat legacy/ as deprecated."],
                "pages": [
                    {
                        "title": "Architecture",
                        "purpose": "System overview.",
                        "modules": ["src/core"],
                        "page_notes": ["Bus lives in core/bus.ts."],
                    }
                ],
            }
        )
    )

    steering = load_user_steering(tmp_path)
    assert steering is not None
    assert steering.repo_notes == ["Treat legacy/ as deprecated."]
    assert len(steering.pages) == 1
    page = steering.pages[0]
    assert page.title == "Architecture"
    assert page.purpose == "System overview."
    assert page.modules == ["src/core"]
    assert page.page_notes == ["Bus lives in core/bus.ts."]


def test_load_tolerates_partial_page(tmp_path):
    from worker.pipeline.user_steering import load_user_steering

    cfg_dir = tmp_path / ".autowiki"
    cfg_dir.mkdir()
    (cfg_dir / "wiki.json").write_text(json.dumps({"pages": [{"title": "Only"}]}))

    steering = load_user_steering(tmp_path)
    assert steering is not None
    assert steering.pages[0].title == "Only"
    assert steering.pages[0].purpose is None
    assert steering.pages[0].modules == []
    assert steering.pages[0].page_notes == []


def test_load_returns_none_on_invalid_json(tmp_path, caplog):
    from worker.pipeline.user_steering import load_user_steering

    cfg_dir = tmp_path / ".autowiki"
    cfg_dir.mkdir()
    (cfg_dir / "wiki.json").write_text("{ not json")

    assert load_user_steering(tmp_path) is None
    assert any("invalid" in rec.message.lower() for rec in caplog.records)


def test_assign_by_modules_groups_files_by_prefix():
    from worker.pipeline.user_steering import UserPageSpec, assign_by_modules

    pages = [
        UserPageSpec(title="Core", modules=["src/core"]),
        UserPageSpec(title="API", modules=["src/api", "src/routes"]),
    ]
    all_files = [
        "src/core/bus.ts",
        "src/core/util.ts",
        "src/api/server.ts",
        "src/routes/index.ts",
        "src/misc/other.ts",
    ]
    assignments, unassigned = assign_by_modules(pages, all_files)
    assert assignments["Core"] == ["src/core/bus.ts", "src/core/util.ts"]
    assert assignments["API"] == ["src/api/server.ts", "src/routes/index.ts"]
    assert unassigned == ["src/misc/other.ts"]


async def test_full_index_reads_autowiki_wiki_json(tmp_path, monkeypatch):
    """run_full_index reads `.autowiki/wiki.json` during Stage 1 and forwards
    the UserSteering object to generate_wiki_plan."""
    import json as _json
    from unittest.mock import AsyncMock, MagicMock, patch

    from shared.config import reset_config
    from shared.database import dispose_db, get_session, init_db
    from shared.models import Job, Repository
    from worker.jobs import run_full_index

    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    reset_config()

    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="o", name="n", status="pending"))
        s.add(Job(id="j1", repo_id="r1", type="full_index", status="queued"))
        await s.commit()

    clone_root = tmp_path / "repos" / "r1" / "clone"
    (clone_root / ".autowiki").mkdir(parents=True)
    (clone_root / ".autowiki" / "wiki.json").write_text(
        _json.dumps({"repo_notes": ["N"]})
    )

    captured: dict = {}

    async def _fake_plan(*args, **kwargs):
        captured["user_steering"] = kwargs.get("user_steering")
        from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

        return WikiPlan(pages=[WikiPageSpec(title="Overview", purpose="Test")])

    with (
        patch(
            "worker.jobs.clone_or_fetch",
            new=AsyncMock(return_value=("abc", "main")),
        ),
        patch("worker.jobs.fetch_github_metadata", new=AsyncMock(return_value={})),
        patch("worker.jobs.filter_files", return_value=[]),
        patch("worker.jobs.extract_readme", return_value=""),
        patch(
            "worker.jobs.analyze_all_files",
            return_value=MagicMock(files={}, to_llm_summary=lambda **k: ""),
        ),
        patch(
            "worker.jobs.build_dependency_graph",
            return_value=MagicMock(clusters=[], edges={}),
        ),
        patch("worker.jobs.build_rag_index", new=AsyncMock()),
        patch("worker.jobs.make_llm_provider"),
        patch("worker.jobs.make_fast_llm_provider"),
        patch(
            "worker.jobs.make_embedding_provider",
            return_value=MagicMock(dimension=8),
        ),
        patch("worker.jobs.generate_wiki_plan", new=_fake_plan),
        patch("worker.jobs.compute_generation_order", return_value=[]),
    ):
        try:
            await run_full_index({}, "r1", "j1", "o", "n", clone_root=clone_root)
        finally:
            await dispose_db(db_path)
            reset_config()

    assert captured["user_steering"] is not None
    assert captured["user_steering"].repo_notes == ["N"]


async def test_planner_skips_phase1_when_user_provides_pages(mock_llm):
    """When user_steering.pages is non-empty, Phase 1 LLM outline is skipped."""
    from unittest.mock import MagicMock

    from worker.pipeline.ast_analysis import FileAnalysis
    from worker.pipeline.user_steering import UserPageSpec, UserSteering
    from worker.pipeline.wiki_planner import generate_wiki_plan

    steering = UserSteering(
        repo_notes=["Focus on the core module."],
        pages=[
            UserPageSpec(title="Core", purpose="Core module.", modules=["src/core"]),
            UserPageSpec(title="API", purpose="API layer."),
        ],
    )

    # LLM is NOT called — assign_by_modules handles file assignment directly
    file_analysis = FileAnalysis(files={"src/core/main.py": MagicMock(entities=[])})

    plan = await generate_wiki_plan(
        file_analysis=file_analysis,
        repo_name="testrepo",
        llm=mock_llm,
        user_steering=steering,
    )

    assert len(plan.pages) == 2
    assert plan.pages[0].title == "Core"
    assert plan.pages[1].title == "API"
    # repo_notes transferred
    assert any(
        "Focus on the core module" in (n.get("content") or "") for n in plan.repo_notes
    )
    mock_llm.generate_structured.assert_not_called()


async def test_planner_injects_page_notes_from_user_steering(mock_llm):
    """page_notes from user_steering are merged onto the matching WikiPageSpec."""
    from worker.pipeline.ast_analysis import FileAnalysis
    from worker.pipeline.user_steering import UserPageSpec, UserSteering
    from worker.pipeline.wiki_planner import generate_wiki_plan

    steering = UserSteering(
        pages=[
            UserPageSpec(
                title="Core", purpose="Core.", page_notes=["Key invariant: X."]
            ),
        ],
    )

    file_analysis = FileAnalysis(files={})

    plan = await generate_wiki_plan(
        file_analysis=file_analysis,
        repo_name="testrepo",
        llm=mock_llm,
        user_steering=steering,
    )

    core_page = next(p for p in plan.pages if p.title == "Core")
    note_contents = [n.get("content", "") for n in core_page.page_notes]
    assert any("Key invariant: X." in c for c in note_contents)


def test_to_api_structure_includes_has_user_notes():
    """WikiPageSpec with non-empty page_notes has has_user_notes=True in API."""
    from worker.pipeline.wiki_planner import WikiPageSpec, WikiPlan

    plan = WikiPlan(
        pages=[
            WikiPageSpec(
                title="Core",
                purpose="Core.",
                page_notes=[{"content": "Key invariant: X."}],
            ),
            WikiPageSpec(title="Overview", purpose="Top."),
        ]
    )
    structure = plan.to_api_structure()
    pages = structure["pages"]
    core = next(p for p in pages if p["title"] == "Core")
    overview = next(p for p in pages if p["title"] == "Overview")
    assert core["has_user_notes"] is True
    assert overview["has_user_notes"] is False
