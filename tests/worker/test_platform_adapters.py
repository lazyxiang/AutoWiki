from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from worker.platform.base import (
    AuthenticationError,
    PrivateRepoError,
    RepoMetadata,
    UnsupportedPlatformError,
)
from worker.platform.bitbucket import BitbucketPlatform
from worker.platform.github import GitHubPlatform
from worker.platform.gitlab import GitLabPlatform
from worker.platform.registry import detect_platform, get_platform_by_name


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


_gl = GitLabPlatform()


# ── parse_url ────────────────────────────────────────────────────────


def test_gitlab_parse_url_simple():
    assert _gl.parse_url("https://gitlab.com/group/repo") == ("group", "repo")


def test_gitlab_parse_url_subgroup():
    assert _gl.parse_url("https://gitlab.com/group/sub/repo") == ("group/sub", "repo")


def test_gitlab_parse_url_deep_subgroup():
    assert _gl.parse_url("gitlab.com/a/b/c/repo") == ("a/b/c", "repo")


def test_gitlab_parse_url_invalid():
    with pytest.raises(ValueError):
        _gl.parse_url("https://gitlab.com/only-one")


# ── authenticated_clone_url ──────────────────────────────────────────


def test_gitlab_clone_url_with_token():
    assert (
        _gl.authenticated_clone_url("group/sub", "repo", "tok")
        == "https://oauth2:tok@gitlab.com/group/sub/repo.git"
    )


def test_gitlab_clone_url_no_token():
    assert (
        _gl.authenticated_clone_url("group", "repo", None)
        == "https://gitlab.com/group/repo.git"
    )


# ── fetch_metadata ───────────────────────────────────────────────────


def _make_gitlab_client(project_data: dict, lang_data: dict, project_status: int = 200):
    proj_resp = MagicMock()
    proj_resp.status_code = project_status
    if project_status >= 400:
        proj_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=proj_resp
        )
    else:
        proj_resp.raise_for_status = MagicMock()
    proj_resp.json.return_value = project_data

    lang_resp = MagicMock()
    lang_resp.status_code = 200
    lang_resp.raise_for_status = MagicMock()
    lang_resp.json.return_value = lang_data

    calls = [proj_resp, lang_resp]
    call_idx = 0

    async def _get(url, **kwargs):
        nonlocal call_idx
        r = calls[call_idx]
        call_idx += 1
        return r

    mock_client = AsyncMock()
    mock_client.get = _get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


async def test_gitlab_fetch_metadata_public():
    client = _make_gitlab_client(
        {
            "visibility": "public",
            "description": "GL repo",
            "star_count": 5,
            "default_branch": "main",
        },
        {"Python": 90.0, "Shell": 10.0},
    )
    with patch("worker.platform.gitlab.httpx.AsyncClient", return_value=client):
        meta = await _gl.fetch_metadata("group", "repo", "token")
    assert meta.language == "Python"
    assert meta.is_private is False
    assert meta.stars == 5


async def test_gitlab_fetch_metadata_private_no_token():
    client = _make_gitlab_client({}, {}, project_status=404)
    with patch("worker.platform.gitlab.httpx.AsyncClient", return_value=client):
        with pytest.raises(PrivateRepoError):
            await _gl.fetch_metadata("group", "private-repo", None)


async def test_gitlab_fetch_metadata_bad_token():
    client = _make_gitlab_client({}, {}, project_status=401)
    with patch("worker.platform.gitlab.httpx.AsyncClient", return_value=client):
        with pytest.raises(AuthenticationError):
            await _gl.fetch_metadata("group", "repo", "bad")


_bb = BitbucketPlatform()


def test_bitbucket_parse_url_full():
    assert _bb.parse_url("https://bitbucket.org/owner/repo") == ("owner", "repo")


def test_bitbucket_parse_url_no_scheme():
    assert _bb.parse_url("bitbucket.org/owner/repo") == ("owner", "repo")


def test_bitbucket_parse_url_invalid():
    with pytest.raises(ValueError):
        _bb.parse_url("https://bitbucket.org/only-one")


def test_bitbucket_clone_url_with_token():
    assert (
        _bb.authenticated_clone_url("owner", "repo", "tok")
        == "https://x-token-auth:tok@bitbucket.org/owner/repo.git"
    )


def test_bitbucket_clone_url_no_token():
    assert (
        _bb.authenticated_clone_url("owner", "repo", None)
        == "https://bitbucket.org/owner/repo.git"
    )


def _make_bitbucket_client(json_data: dict, status_code: int = 200):
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


async def test_bitbucket_fetch_metadata_public():
    client = _make_bitbucket_client(
        {
            "is_private": False,
            "description": "BB repo",
            "language": "javascript",
            "mainbranch": {"name": "main"},
        }
    )
    with patch("worker.platform.bitbucket.httpx.AsyncClient", return_value=client):
        meta = await _bb.fetch_metadata("owner", "repo", None)
    assert meta.is_private is False
    assert meta.language == "javascript"
    assert meta.stars == 0


async def test_bitbucket_fetch_metadata_private_no_token():
    client = _make_bitbucket_client({}, status_code=401)
    with patch("worker.platform.bitbucket.httpx.AsyncClient", return_value=client):
        with pytest.raises(PrivateRepoError):
            await _bb.fetch_metadata("owner", "repo", None)


async def test_bitbucket_fetch_metadata_bad_token():
    client = _make_bitbucket_client({}, status_code=403)
    with patch("worker.platform.bitbucket.httpx.AsyncClient", return_value=client):
        with pytest.raises(AuthenticationError):
            await _bb.fetch_metadata("owner", "repo", "bad")


# ── registry tests ───────────────────────────────────────────────────


def test_detect_platform_github():
    assert isinstance(detect_platform("https://github.com/owner/repo"), GitHubPlatform)


def test_detect_platform_gitlab():
    assert isinstance(detect_platform("https://gitlab.com/group/repo"), GitLabPlatform)


def test_detect_platform_bitbucket():
    assert isinstance(
        detect_platform("https://bitbucket.org/owner/repo"), BitbucketPlatform
    )


def test_detect_platform_unsupported():
    with pytest.raises(UnsupportedPlatformError):
        detect_platform("https://codeberg.org/owner/repo")


def test_get_platform_by_name_github():
    assert isinstance(get_platform_by_name("github"), GitHubPlatform)


def test_get_platform_by_name_gitlab():
    assert isinstance(get_platform_by_name("gitlab"), GitLabPlatform)


def test_get_platform_by_name_bitbucket():
    assert isinstance(get_platform_by_name("bitbucket"), BitbucketPlatform)


def test_get_platform_by_name_unknown():
    with pytest.raises(UnsupportedPlatformError):
        get_platform_by_name("unknown")
