from __future__ import annotations

import asyncio
import json as _json

import httpx
import typer

from worker.pipeline.ingestion import get_repo_hash
from worker.platform.base import UnsupportedPlatformError
from worker.platform.registry import detect_platform


def chat_cmd(
    url: str = typer.Argument(..., help="Repository URL"),
    question: str = typer.Argument(..., help="Question to ask about the repository"),
    api_url: str = typer.Option("http://127.0.0.1:3001", envvar="AUTOWIKI_API_URL"),
):
    """Ask a single question about an indexed repository."""
    try:
        platform = detect_platform(url)
        owner, name = platform.parse_url(url)
    except (ValueError, UnsupportedPlatformError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    repo_id = get_repo_hash(platform.name, owner, name)

    try:
        repo_resp = httpx.get(f"{api_url}/api/repos/{repo_id}", timeout=10)
    except httpx.ConnectError:
        typer.echo(
            "Error: cannot connect to AutoWiki API. Is the server running?", err=True
        )
        raise typer.Exit(1)
    if repo_resp.status_code == 404:
        typer.echo("Repository not found. Run `autowiki index` first.", err=True)
        raise typer.Exit(1)
    if repo_resp.status_code >= 400:
        typer.echo(f"API error {repo_resp.status_code}: {repo_resp.text}", err=True)
        raise typer.Exit(1)
    if repo_resp.json().get("status") != "ready":
        typer.echo("Repository is not ready. Wait for indexing to complete.", err=True)
        raise typer.Exit(1)

    try:
        session_resp = httpx.post(f"{api_url}/api/repos/{repo_id}/chat", timeout=10)
    except httpx.ConnectError:
        typer.echo(
            "Error: cannot connect to AutoWiki API. Is the server running?", err=True
        )
        raise typer.Exit(1)
    session_resp.raise_for_status()
    session_id = session_resp.json()["session_id"]

    import websockets

    ws_url = api_url.replace("http://", "ws://").replace("https://", "wss://")

    async def _ask() -> str:
        uri = f"{ws_url}/ws/repos/{repo_id}/chat/{session_id}"
        async with websockets.connect(uri) as ws:
            await ws.send(_json.dumps({"content": question}))
            chunks = []
            while True:
                msg = _json.loads(await ws.recv())
                if msg["type"] == "chunk":
                    chunks.append(msg["content"])
                elif msg["type"] == "done":
                    break
                elif msg["type"] == "error":
                    raise RuntimeError(msg["content"])
            return "".join(chunks)

    try:
        answer = asyncio.run(_ask())
        typer.echo(answer)
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Connection error: {e}", err=True)
        raise typer.Exit(1)
