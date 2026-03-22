import typer
import json
from pathlib import Path
from shared.config import get_config

config_app = typer.Typer(help="Manage AutoWiki configuration")


@config_app.command("show")
def show():
    """Show current configuration."""
    cfg = get_config()
    typer.echo(json.dumps(cfg.model_dump(), indent=2, default=str))


@config_app.command("set")
def set_value(
    key: str = typer.Argument(..., help="Dot-separated key, e.g. llm.provider"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a configuration value in ~/.autowiki/autowiki.yml."""
    try:
        import yaml
    except ImportError:
        typer.echo("Error: PyYAML not installed", err=True)
        raise typer.Exit(1)
    config_path = Path.home() / ".autowiki" / "autowiki.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    if existing is None:
        existing = {}
    keys = key.split(".")
    d = existing
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value
    config_path.write_text(yaml.dump(existing, default_flow_style=False))
    typer.echo(f"Set {key} = {value} in {config_path}")
