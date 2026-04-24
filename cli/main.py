import typer

from cli.commands.config_cmd import config_app
from cli.commands.index import index_cmd
from cli.commands.list_repos import list_cmd
from cli.commands.refresh import refresh_cmd
from cli.commands.research_cmd import research_cmd
from cli.commands.serve import serve_cmd
from cli.commands.validate_plan import validate_plan_cmd

app = typer.Typer(name="autowiki", help="AutoWiki — AI-powered wiki generator")
app.command("index")(index_cmd)
app.command("list")(list_cmd)
app.command("serve")(serve_cmd)
app.add_typer(config_app, name="config")
app.command("refresh")(refresh_cmd)
app.command("research")(research_cmd)
app.command("validate-plan")(validate_plan_cmd)

if __name__ == "__main__":
    app()
