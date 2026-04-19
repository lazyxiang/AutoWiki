"""``autowiki validate-plan`` — offline planner introspection.

Reads ``{data_dir}/repos/{repo}/ast/wiki_plan.json`` and reports
coverage, per-page size distribution, and any validation failures
without running the pipeline.  No LLM calls, no git clone, no writes.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import typer

from shared.config import Config
from worker.pipeline.wiki_planner import (
    WikiPageSpec,
    WikiPlan,
    validate_wiki_plan,
)


def _load_plan(plan_path: Path) -> tuple[dict, WikiPlan]:
    data = json.loads(plan_path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Plan root must be a JSON object")
    # Normalise into WikiPlan after validation or for stats use.
    # Note: validate_plan_cmd calls validate_wiki_plan(data) first.
    pages = [
        WikiPageSpec(
            title=p.get("title", ""),
            purpose=p.get("purpose", ""),
            parent=p.get("parent"),
            files=p.get("files", []),
        )
        for p in data.get("pages", [])
    ]
    return data, WikiPlan(repo_notes=data.get("repo_notes", []), pages=pages)


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
    data_dir = Config().data_dir
    plan_path = data_dir / "repos" / repo / "ast" / "wiki_plan.json"
    if not plan_path.is_file():
        typer.echo(f"Error: wiki plan not found at {plan_path}", err=True)
        raise typer.Exit(1)

    try:
        raw_plan, plan = _load_plan(plan_path)
        validate_wiki_plan(
            raw_plan,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        typer.echo(f"VALIDATION FAILURE: {exc}", err=False)
        raise typer.Exit(1)

    total_primary = sum(len(p.files) for p in plan.pages)
    sizes = [len(p.files) for p in plan.pages]

    typer.echo(f"Pages: {len(plan.pages)}")
    typer.echo(f"Primary files: {total_primary}")
    typer.echo("")
    typer.echo("Per-page file distribution:")
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
        typer.echo(f"  - {p.title}: files={len(p.files)}")
    typer.echo("")

    def _locality_score(page: WikiPageSpec) -> float:
        if not page.files:
            return 1.0
        counts: dict[str, int] = {}
        for f in page.files:
            key = f.split("/", 1)[0] if "/" in f else "(root)"
            counts[key] = counts.get(key, 0) + 1
        top = max(counts.values())
        return top / len(page.files)

    typer.echo("Locality score (fraction of primary files in top directory):")
    for p in plan.pages:
        score = _locality_score(p)
        typer.echo(f"  - {p.title}: {score:.2f}")
    typer.echo("")
    typer.echo("Validation: OK")
