import typer
from cli.commands.index import index_cmd
from cli.commands.list_repos import list_cmd
from cli.commands.serve import serve_cmd
from cli.commands.config_cmd import config_app

app = typer.Typer(name="autowiki", help="AutoWiki — AI-powered wiki generator")
app.command("index")(index_cmd)
app.command("list")(list_cmd)
app.command("serve")(serve_cmd)
app.add_typer(config_app, name="config")

if __name__ == "__main__":
    app()
