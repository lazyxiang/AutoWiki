from __future__ import annotations

import hashlib
import time

import httpx
import typer

from worker.pipeline.ingestion import parse_github_url

API_BASE = "http://localhost:3001"


def refresh_cmd(url: str = typer.Argument(..., help="GitHub repo URL")):
    """Trigger incremental refresh for an indexed repository."""
    try:
        owner, name = parse_github_url(url)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    repo_id = hashlib.sha256(f"github:{owner}/{name}".encode()).hexdigest()[:16]
    resp = httpx.post(f"{API_BASE}/api/repos/{repo_id}/refresh")
    if resp.status_code == 404:
        typer.echo("Repository not found. Run `autowiki index` first.", err=True)
        raise typer.Exit(1)
    if resp.status_code == 409:
        typer.echo("Repository is currently being indexed. Try again later.", err=True)
        raise typer.Exit(1)
    resp.raise_for_status()

    job_id = resp.json()["job_id"]
    typer.echo(f"Refresh job queued: {job_id}")

    with typer.progressbar(length=100, label="Refreshing") as progress:
        last = 0
        while True:
            status_resp = httpx.get(f"{API_BASE}/api/jobs/{job_id}")
            status_resp.raise_for_status()
            data = status_resp.json()
            current = data.get("progress", 0)
            progress.update(current - last)
            last = current
            if data.get("status") in ("done", "failed"):
                break
            time.sleep(2)

    if data.get("status") == "failed":
        typer.echo(f"\nRefresh failed: {data.get('error', 'unknown error')}", err=True)
        raise typer.Exit(1)
    typer.echo("\nRefresh complete.")
