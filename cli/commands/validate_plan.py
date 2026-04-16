"""``autowiki validate-plan`` — offline planner introspection.

Reads ``{data_dir}/repos/{repo}/ast/wiki_plan.json`` and reports
coverage, per-page size distribution, and any validation failures
without running the pipeline.  No LLM calls, no git clone, no writes.
"""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

import typer

from worker.pipeline.wiki_planner import (
    WikiPageSpec,
    WikiPlan,
    validate_wiki_plan,
)


def _data_dir() -> Path:
    raw = os.environ.get("AUTOWIKI_DATA_DIR")
    if raw:
        return Path(raw)
    return Path.home() / ".autowiki"


def _load_plan(plan_path: Path) -> WikiPlan:
    data = json.loads(plan_path.read_text())
    pages = [
        WikiPageSpec(
            title=p["title"],
            purpose=p.get("purpose", ""),
            parent=p.get("parent"),
            files=p.get("files", []),
            secondary_files=p.get("secondary_files", []),
        )
        for p in data.get("pages", [])
    ]
    return WikiPlan(repo_notes=data.get("repo_notes", []), pages=pages)


def _percentile(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(p * (len(ordered) - 1)))
    return float(ordered[idx])


def validate_plan_cmd(
    repo: str = typer.Argument(
        ..., help="Repo identifier as stored under ~/.autowiki/repos/"
    ),
) -> None:
    """Report coverage, sizes, and validation status of a stored plan."""
    data_dir = _data_dir()
    plan_path = data_dir / "repos" / repo / "ast" / "wiki_plan.json"
    if not plan_path.is_file():
        typer.echo(f"Error: wiki plan not found at {plan_path}", err=True)
        raise typer.Exit(1)

    plan = _load_plan(plan_path)
    total_primary = sum(len(p.files) for p in plan.pages)
    total_secondary = sum(len(p.secondary_files) for p in plan.pages)
    sizes = [len(p.files) for p in plan.pages]

    typer.echo(f"Pages: {len(plan.pages)}")
    typer.echo(f"Primary files: {total_primary}")
    typer.echo(f"Secondary assignments: {total_secondary}")
    typer.echo("")
    typer.echo("Per-page primary file distribution:")
    typer.echo(
        f"  min={min(sizes, default=0)} "
        f"p50={_percentile(sizes, 0.5):.0f} "
        f"p90={_percentile(sizes, 0.9):.0f} "
        f"max={max(sizes, default=0)} "
        f"mean={statistics.mean(sizes) if sizes else 0:.1f}"
    )
    typer.echo("")
    typer.echo("Per-page breakdown:")
    for p in plan.pages:
        typer.echo(
            f"  - {p.title}: primary={len(p.files)} secondary={len(p.secondary_files)}"
        )
    typer.echo("")

    try:
        validate_wiki_plan(
            {
                "pages": [
                    {
                        "title": p.title,
                        "purpose": p.purpose,
                        "parent": p.parent,
                        "files": p.files,
                        "secondary_files": p.secondary_files,
                    }
                    for p in plan.pages
                ]
            },
        )
        typer.echo("Validation: OK")
    except ValueError as exc:
        typer.echo(f"VALIDATION FAILURE: {exc}", err=False)
        raise typer.Exit(1)
