import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock
import httpx

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
    config_file = tmp_path / ".autowiki" / "autowiki.yml"
    with patch("pathlib.Path.home", return_value=tmp_path):
        result = cli.invoke(app, ["config", "set", "llm.provider", "openai"])
    assert result.exit_code == 0
    assert "openai" in result.output
