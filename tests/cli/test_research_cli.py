"""CLI tests for `autowiki research`."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cli.main import app

pytestmark = pytest.mark.skipif(
    False,
    reason="placeholder — B5 disabled-state tests live here",
)


def test_research_command_exits_nonzero():
    """B5: `autowiki research` exits non-zero with a disabled message."""
    runner = CliRunner()
    result = runner.invoke(app, ["research", "github.com/x/y", "what is this?"])
    assert result.exit_code != 0
    assert "temporarily unavailable" in result.output.lower()
