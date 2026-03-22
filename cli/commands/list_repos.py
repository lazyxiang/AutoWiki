import typer
import httpx


def list_cmd(
    api_url: str = typer.Option("http://127.0.0.1:3001", envvar="AUTOWIKI_API_URL"),
):
    """List all indexed repositories."""
    try:
        resp = httpx.get(f"{api_url}/api/repos", timeout=10)
        resp.raise_for_status()
        repos = resp.json().get("repos", [])
        if not repos:
            typer.echo("No repositories indexed yet.")
            return
        for r in repos:
            typer.echo(f"{r['owner']}/{r['name']}  [{r['status']}]")
    except httpx.HTTPStatusError as e:
        typer.echo(f"Error: {e.response.text}", err=True)
        raise typer.Exit(1)
    except httpx.ConnectError:
        typer.echo("Error: cannot connect to AutoWiki API.", err=True)
        raise typer.Exit(1)
