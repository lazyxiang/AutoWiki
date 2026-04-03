from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner():
    from cli.main import app

    return CliRunner(), app


def test_index_success(runner):
    cli, app = runner
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"repo_id": "abc", "job_id": "j1", "status": "queued"}
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.post", return_value=mock_resp):
        result = cli.invoke(app, ["index", "github.com/psf/requests"])
    assert result.exit_code == 0
    assert "j1" in result.output


def test_index_connection_error(runner):
    cli, app = runner
    with patch("httpx.post", side_effect=httpx.ConnectError("no server")):
        result = cli.invoke(app, ["index", "github.com/psf/requests"])
    assert result.exit_code == 1
    assert "cannot connect" in result.output.lower()


def test_list_empty(runner):
    cli, app = runner
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"repos": []}
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=mock_resp):
        result = cli.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No repositories" in result.output


def test_config_show(runner):
    cli, app = runner
    result = cli.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "anthropic" in result.output  # default provider


def test_config_set(runner, tmp_path):
    cli, app = runner
    with patch("pathlib.Path.home", return_value=tmp_path):
        result = cli.invoke(app, ["config", "set", "llm.provider", "openai"])
    assert result.exit_code == 0
    assert "openai" in result.output


def test_refresh_cmd_success(runner):
    cli, app = runner
    with (
        patch("cli.commands.refresh.httpx.post") as mock_post,
        patch("cli.commands.refresh.httpx.get") as mock_get,
    ):
        mock_post.return_value = MagicMock(
            status_code=202,
            json=lambda: {"repo_id": "abc", "job_id": "job1", "status": "queued"},
        )
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"status": "done", "progress": 100}
        )
        result = cli.invoke(app, ["refresh", "github.com/psf/requests"])
    assert result.exit_code == 0
    assert "Refresh complete" in result.output


def test_chat_cmd_prints_response(runner):
    cli, app = runner

    def mock_run(coro):
        coro.close()
        return "It does foo things."

    with (
        patch("cli.commands.chat_cmd.httpx.get") as mock_get,
        patch("cli.commands.chat_cmd.httpx.post") as mock_post,
        patch("cli.commands.chat_cmd.asyncio.run", side_effect=mock_run),
    ):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"id": "r1", "status": "ready"}
        )
        mock_post.return_value = MagicMock(
            status_code=201, json=lambda: {"session_id": "s1"}
        )
        result = cli.invoke(
            app, ["chat", "github.com/psf/requests", "What does foo do?"]
        )
    assert result.exit_code == 0
    assert "It does foo things." in result.output
