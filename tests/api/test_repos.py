import pytest
from unittest.mock import patch, AsyncMock


async def test_post_repos_returns_202(client):
    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock) as mock_eq:
        mock_eq.return_value = "job-uuid-1"
        resp = await client.post("/api/repos", json={"url": "https://github.com/psf/requests"})
    assert resp.status_code == 202
    body = resp.json()
    assert "repo_id" in body
    assert "job_id" in body
    assert body["status"] == "queued"


async def test_post_repos_bad_url(client):
    resp = await client.post("/api/repos", json={"url": "not-a-github-url"})
    assert resp.status_code == 422


async def test_get_repo_not_found(client):
    resp = await client.get("/api/repos/doesnotexist")
    assert resp.status_code == 404


async def test_list_repos_empty(client):
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    assert resp.json() == {"repos": []}


async def test_list_repos_after_index(client):
    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock) as mock_eq:
        mock_eq.return_value = "job-uuid-2"
        await client.post("/api/repos", json={"url": "https://github.com/psf/requests"})
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    repos = resp.json()["repos"]
    assert len(repos) == 1
    assert repos[0]["owner"] == "psf"
