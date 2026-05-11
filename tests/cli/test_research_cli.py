"""CLI tests for `autowiki research`."""

from __future__ import annotations

from typer.testing import CliRunner

from cli.main import app


def test_research_command_exits_nonzero():
    """B5: `autowiki research` exits non-zero with a disabled message."""
    runner = CliRunner()
    result = runner.invoke(app, ["research", "github.com/x/y", "what is this?"])
    assert result.exit_code != 0
    assert "temporarily unavailable" in result.output.lower()
