from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from worker.platform.base import (
    AuthenticationError,
    PrivateRepoError,
    RepoMetadata,
    UnsupportedPlatformError,
)
from worker.platform.github import GitHubPlatform


def test_repo_metadata_fields():
    meta = RepoMetadata(
        owner="acme",
        name="core",
        description="Core lib",
        stars=10,
        language="Python",
        default_branch="main",
        is_private=False,
    )
    assert meta.owner == "acme"
    assert meta.is_private is False


def test_private_repo_error_is_exception():
    with pytest.raises(PrivateRepoError):
        raise PrivateRepoError("no token")


def test_authentication_error_is_exception():
    with pytest.raises(AuthenticationError):
        raise AuthenticationError("bad token")


def test_unsupported_platform_error_is_exception():
    with pytest.raises(UnsupportedPlatformError):
        raise UnsupportedPlatformError("unknown host")


_gh = GitHubPlatform()


# ── parse_url ────────────────────────────────────────────────────────


def test_github_parse_url_full():
    assert _gh.parse_url("https://github.com/psf/requests") == ("psf", "requests")


def test_github_parse_url_no_scheme():
    assert _gh.parse_url("github.com/psf/requests") == ("psf", "requests")


def test_github_parse_url_git_suffix():
    assert _gh.parse_url("github.com/psf/requests.git") == ("psf", "requests")


def test_github_parse_url_invalid():
    with pytest.raises(ValueError):
        _gh.parse_url("https://github.com/only-one-segment")


# ── authenticated_clone_url ──────────────────────────────────────────


def test_github_clone_url_with_token():
    assert (
        _gh.authenticated_clone_url("owner", "repo", "tok")
        == "https://tok@github.com/owner/repo.git"
    )


def test_github_clone_url_no_token():
    assert (
        _gh.authenticated_clone_url("owner", "repo", None)
        == "https://github.com/owner/repo.git"
    )


# ── fetch_metadata ───────────────────────────────────────────────────


def _make_github_client(json_data: dict, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_resp
        )
    else:
        mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = json_data
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


async def test_github_fetch_metadata_public():
    client = _make_github_client(
        {
            "private": False,
            "description": "HTTP for Humans",
            "stargazers_count": 50000,
            "language": "Python",
            "default_branch": "main",
        }
    )
    with patch("worker.platform.github.httpx.AsyncClient", return_value=client):
        meta = await _gh.fetch_metadata("psf", "requests", None)
    assert meta.description == "HTTP for Humans"
    assert meta.stars == 50000
    assert meta.language == "Python"
    assert meta.is_private is False


async def test_github_fetch_metadata_private_no_token():
    client = _make_github_client({}, status_code=404)
    with patch("worker.platform.github.httpx.AsyncClient", return_value=client):
        with pytest.raises(PrivateRepoError):
            await _gh.fetch_metadata("owner", "private-repo", None)


async def test_github_fetch_metadata_bad_token():
    client = _make_github_client({}, status_code=401)
    with patch("worker.platform.github.httpx.AsyncClient", return_value=client):
        with pytest.raises(AuthenticationError):
            await _gh.fetch_metadata("owner", "private-repo", "bad-token")
