from __future__ import annotations

import asyncio
import hashlib
import json as _json

import httpx
import typer

from worker.pipeline.ingestion import parse_github_url

API_BASE = "http://localhost:3001"


def chat_cmd(
    url: str = typer.Argument(..., help="GitHub repo URL"),
    question: str = typer.Argument(..., help="Question to ask about the repository"),
):
    """Ask a single question about an indexed repository."""
    try:
        owner, name = parse_github_url(url)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    repo_id = hashlib.sha256(f"github:{owner}/{name}".encode()).hexdigest()[:16]

    repo_resp = httpx.get(f"{API_BASE}/api/repos/{repo_id}")
    if repo_resp.status_code == 404:
        typer.echo("Repository not found. Run `autowiki index` first.", err=True)
        raise typer.Exit(1)
    if repo_resp.json().get("status") != "ready":
        typer.echo("Repository is not ready. Wait for indexing to complete.", err=True)
        raise typer.Exit(1)

    session_resp = httpx.post(f"{API_BASE}/api/repos/{repo_id}/chat")
    session_resp.raise_for_status()
    session_id = session_resp.json()["session_id"]

    import websockets

    async def _ask() -> str:
        uri = f"ws://localhost:3001/ws/repos/{repo_id}/chat/{session_id}"
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

    answer = asyncio.run(_ask())
    typer.echo(answer)
