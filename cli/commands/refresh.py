from __future__ import annotations

import time

import httpx
import typer

from worker.pipeline.ingestion import get_repo_hash
from worker.platform.base import UnsupportedPlatformError
from worker.platform.registry import detect_platform


def refresh_cmd(
    url: str = typer.Argument(..., help="Repository URL"),
    api_url: str = typer.Option("http://127.0.0.1:3001", envvar="AUTOWIKI_API_URL"),
):
    """Trigger incremental refresh for an indexed repository."""
    try:
        platform = detect_platform(url)
        owner, name = platform.parse_url(url)
    except (ValueError, UnsupportedPlatformError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    repo_id = get_repo_hash(platform.name, owner, name)

    try:
        resp = httpx.post(f"{api_url}/api/repos/{repo_id}/refresh", timeout=10)
    except httpx.ConnectError:
        typer.echo(
            "Error: cannot connect to AutoWiki API. Is the server running?", err=True
        )
        raise typer.Exit(1)
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
            try:
                status_resp = httpx.get(f"{api_url}/api/jobs/{job_id}", timeout=10)
                status_resp.raise_for_status()
            except (httpx.ConnectError, httpx.RequestError) as e:
                typer.echo(f"\nConnection error while polling: {e}", err=True)
                raise typer.Exit(1)
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
