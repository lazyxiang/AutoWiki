import os
import subprocess
import sys
from pathlib import Path

import typer


def serve_cmd(
    port: int = typer.Option(3000, "--port", "-p", help="Web UI port"),
    api_port: int = typer.Option(3001, "--api-port", help="API port"),
):
    """Start the full AutoWiki stack (API + worker + web UI)."""
    typer.echo("Starting AutoWiki...")
    typer.echo(f"  API:    http://127.0.0.1:{api_port}")
    typer.echo(f"  Web UI: http://127.0.0.1:{port}")
    typer.echo("Press Ctrl+C to stop.\n")
    env = {
        **os.environ,
        "AUTOWIKI_SERVER_PORT": str(api_port),
        "NEXT_PUBLIC_API_URL": f"http://127.0.0.1:{api_port}",
        "INTERNAL_API_URL": f"http://127.0.0.1:{api_port}",
        "PORT": str(port),
    }
    web_dir = Path(__file__).parents[2] / "web"
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            env=env,
        ),
        subprocess.Popen([sys.executable, "-m", "worker.main"], env=env),
        subprocess.Popen(
            ["node", str(web_dir / ".next" / "standalone" / "server.js")],
            env=env,
            cwd=str(web_dir),
        ),
    ]
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
