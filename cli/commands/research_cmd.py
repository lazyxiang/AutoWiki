from __future__ import annotations

import typer


def research_cmd(
    url: str = typer.Argument(..., help="Repository URL"),
    question: str = typer.Argument(..., help="Research question"),
    api_url: str = typer.Option("http://127.0.0.1:3001", envvar="AUTOWIKI_API_URL"),
):
    """Run Deep Research on an indexed repository and print the report."""
    typer.echo(
        "Deep Research is temporarily unavailable while migrating to keyword retrieval "
        "(see issue #43).",
        err=True,
    )
    raise typer.Exit(1)
