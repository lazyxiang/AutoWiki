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
