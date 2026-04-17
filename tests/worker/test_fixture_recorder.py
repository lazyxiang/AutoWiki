"""Tests for the fixture recorder."""

from __future__ import annotations

import json

from worker.pipeline.fixture_recorder import (
    FixtureRecorder,
    is_recording_enabled,
)


def test_is_recording_enabled_respects_env(monkeypatch):
    monkeypatch.delenv("AUTOWIKI_RECORD_PLANNER_FIXTURES", raising=False)
    assert is_recording_enabled() is False
    monkeypatch.setenv("AUTOWIKI_RECORD_PLANNER_FIXTURES", "1")
    assert is_recording_enabled() is True
    monkeypatch.setenv("AUTOWIKI_RECORD_PLANNER_FIXTURES", "0")
    assert is_recording_enabled() is False


async def test_recorder_writes_each_stage_to_a_separate_file(tmp_path):
    rec = FixtureRecorder(root=tmp_path)
    await rec.record_outline([{"title": "A", "purpose": "p"}])
    await rec.record_assignments(primary={"A": ["x.py"]}, secondary={"A": []})
    await rec.record_wiki_plan({"pages": [{"title": "A", "files": ["x.py"]}]})
    assert (tmp_path / "outline.json").exists()
    assert (tmp_path / "assignments.json").exists()
    assert (tmp_path / "wiki_plan.json").exists()
    assert json.loads((tmp_path / "outline.json").read_text()) == [
        {"title": "A", "purpose": "p"}
    ]
    payload = json.loads((tmp_path / "assignments.json").read_text())
    assert payload["primary"] == {"A": ["x.py"]}
    assert payload["secondary"] == {"A": []}


async def test_recorder_is_noop_when_root_is_none(tmp_path):
    rec = FixtureRecorder(root=None)
    await rec.record_outline([{"title": "A", "purpose": "p"}])
    # No files written, no exception raised
    assert list(tmp_path.iterdir()) == []


async def test_recorder_overwrites_existing_files(tmp_path):
    (tmp_path / "outline.json").write_text("stale")
    rec = FixtureRecorder(root=tmp_path)
    await rec.record_outline([{"title": "new"}])
    assert json.loads((tmp_path / "outline.json").read_text()) == [{"title": "new"}]
