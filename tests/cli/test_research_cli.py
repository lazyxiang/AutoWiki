"""CLI tests for `autowiki research`."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from cli.main import app


def test_research_cmd_prints_report():
    """`autowiki research` posts to the API, consumes the WS, prints the report."""
    runner = CliRunner()

    mock_repo_resp = MagicMock()
    mock_repo_resp.status_code = 200
    mock_repo_resp.json.return_value = {"status": "ready"}

    mock_start_resp = MagicMock()
    mock_start_resp.status_code = 202
    mock_start_resp.json.return_value = {
        "job_id": "j1",
        "report_id": "rep1",
        "status": "queued",
    }
    mock_start_resp.raise_for_status = MagicMock()

    events = [
        '{"type": "plan", "plan": [{"query": "Q1", "rationale": "R"}]}',
        '{"type": "step_start", "step_index": 0, "query": "Q1"}',
        '{"type": "step_finding", "step_index": 0, "answer": "A", "sources": []}',
        '{"type": "report", "content": "# Final Report"}',
        '{"type": "done"}',
    ]

    fake_ws = AsyncMock()
    fake_ws.recv.side_effect = events
    fake_ws.__aenter__.return_value = fake_ws
    fake_ws.__aexit__.return_value = None

    with (
        patch("httpx.get", return_value=mock_repo_resp),
        patch("httpx.post", return_value=mock_start_resp),
        patch("websockets.connect", return_value=fake_ws),
    ):
        result = runner.invoke(
            app, ["research", "github.com/o/n", "How does refresh work?"]
        )
    assert result.exit_code == 0
    assert "Final Report" in result.stdout
