"""Integration tests for ``autowiki validate-plan``."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def _write_plan(dirpath: Path, plan: dict) -> None:
    (dirpath / "ast").mkdir(parents=True, exist_ok=True)
    (dirpath / "ast" / "wiki_plan.json").write_text(json.dumps(plan))


def test_validate_plan_reports_coverage_and_page_sizes(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repos" / "owner-repo"
    _write_plan(
        repo_dir,
        {
            "repo_notes": [{"content": ""}],
            "pages": [
                {
                    "title": "Overview",
                    "purpose": "top",
                    "files": ["a.py", "b.py"],
                    "secondary_files": [],
                },
                {
                    "title": "Core",
                    "purpose": "core",
                    "files": [f"core/{i}.py" for i in range(5)],
                    "secondary_files": ["a.py"],
                },
            ],
        },
    )
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))

    result = runner.invoke(app, ["validate-plan", "owner-repo"])
    assert result.exit_code == 0, result.output
    assert "Pages: 2" in result.output
    assert "Primary files: 7" in result.output
    assert "Secondary assignments: 1" in result.output
    # Page sizes
    assert "Overview" in result.output
    assert "Core" in result.output
    # A distribution line exists
    assert "p50" in result.output or "median" in result.output.lower()


def test_validate_plan_reports_validation_failure(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repos" / "bad-repo"
    _write_plan(
        repo_dir,
        {
            "repo_notes": [],
            "pages": [
                {
                    "title": "Overview",
                    "purpose": "top",
                    "files": [f"f{i}.py" for i in range(60)],  # > 50 cap
                    "secondary_files": [],
                }
            ],
        },
    )

    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))

    result = runner.invoke(app, ["validate-plan", "bad-repo"])
    assert result.exit_code == 1
    assert "VALIDATION FAILURE" in result.output
    assert "60 > 50 files" in result.output


def test_validate_plan_missing_repo_returns_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["validate-plan", "does-not-exist"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_validate_plan_reports_locality_score(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repos" / "locality-repo"
    _write_plan(
        repo_dir,
        {
            "repo_notes": [],
            "pages": [
                {
                    "title": "Worker Pipeline",
                    "purpose": "p",
                    "files": [
                        "worker/pipeline/a.py",
                        "worker/pipeline/b.py",
                        "api/routes.py",  # cross-directory — hurts locality
                    ],
                    "secondary_files": [],
                },
                {
                    "title": "Core",
                    "purpose": "p",
                    "files": ["core/a.py", "core/b.py"],
                    "secondary_files": [],
                },
            ],
        },
    )
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["validate-plan", "locality-repo"])
    assert result.exit_code == 0, result.output
    assert "Locality score" in result.output
    # Core page (100% same-dir) should be 1.0
    assert "Core: 1.00" in result.output
