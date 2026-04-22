from __future__ import annotations

import asyncio
import json as _json

import httpx
import typer

from worker.pipeline.ingestion import get_repo_hash
from worker.platform.base import UnsupportedPlatformError
from worker.platform.registry import detect_platform


def research_cmd(
    url: str = typer.Argument(..., help="Repository URL"),
    question: str = typer.Argument(..., help="Research question"),
    api_url: str = typer.Option("http://127.0.0.1:3001", envvar="AUTOWIKI_API_URL"),
):
    """Run Deep Research on an indexed repository and print the report."""
    try:
        platform = detect_platform(url)
        owner, name = platform.parse_url(url)
    except (ValueError, UnsupportedPlatformError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    repo_id = get_repo_hash(platform.name, owner, name)

    try:
        repo_resp = httpx.get(f"{api_url}/api/repos/{repo_id}", timeout=10)
        if repo_resp.status_code == 404:
            typer.echo("Repository not found. Run `autowiki index` first.", err=True)
            raise typer.Exit(1)
        if repo_resp.status_code >= 400:
            typer.echo(f"API error {repo_resp.status_code}: {repo_resp.text}", err=True)
            raise typer.Exit(1)
        if repo_resp.json().get("status") != "ready":
            typer.echo(
                "Repository is not ready. Wait for indexing to complete.", err=True
            )
            raise typer.Exit(1)

        start_resp = httpx.post(
            f"{api_url}/api/repos/{repo_id}/research",
            json={"question": question},
            timeout=10,
        )
        start_resp.raise_for_status()
        job_id = start_resp.json()["job_id"]
    except typer.Exit:
        raise
    except (httpx.HTTPError, ValueError, KeyError) as e:
        typer.echo(f"API error: {e}", err=True)
        raise typer.Exit(1)

    import websockets

    ws_url = api_url.replace("http://", "ws://").replace("https://", "wss://")

    async def _stream() -> str:
        uri = f"{ws_url}/ws/repos/{repo_id}/research/{job_id}"
        final_report = ""
        async with websockets.connect(uri) as ws:
            while True:
                raw = await ws.recv()
                msg = _json.loads(raw)
                mtype = msg["type"]
                if mtype == "plan":
                    typer.echo("\n=== Research Plan ===")
                    for i, step in enumerate(msg["plan"], 1):
                        typer.echo(f"{i}. {step['query']} — {step['rationale']}")
                elif mtype == "step_start":
                    typer.echo(
                        f"\n--- Step {msg['step_index'] + 1}: {msg['query']} ---"
                    )
                elif mtype == "step_finding":
                    typer.echo(msg["answer"])
                elif mtype == "report":
                    final_report = msg["content"]
                elif mtype == "done":
                    break
                elif mtype == "error":
                    raise RuntimeError(msg["content"])
        return final_report

    try:
        report = asyncio.run(_stream())
        typer.echo("\n=== Final Report ===\n")
        typer.echo(report)
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Connection error: {e}", err=True)
        raise typer.Exit(1)
