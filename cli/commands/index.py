import httpx
import typer


def index_cmd(
    url: str = typer.Argument(..., help="GitHub URL, e.g. github.com/owner/repo"),
    force: bool = typer.Option(False, "--force", help="Force full re-index"),
    api_url: str = typer.Option("http://127.0.0.1:3001", envvar="AUTOWIKI_API_URL"),
):
    """Index a GitHub repository."""
    try:
        resp = httpx.post(f"{api_url}/api/repos", json={"url": url, "force": force}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"Indexing started. Job ID: {data['job_id']}")
        typer.echo(f"Track progress: {api_url}/api/jobs/{data['job_id']}")
    except httpx.ConnectError:
        typer.echo(
            "Error: cannot connect to AutoWiki API. Is the server running?", err=True
        )
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        typer.echo(f"Error: {e.response.text}", err=True)
        raise typer.Exit(1)
