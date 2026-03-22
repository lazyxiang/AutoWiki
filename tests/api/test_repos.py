import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock


@pytest.fixture
async def client(tmp_path):
    import os
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)
    from shared.config import reset_config
    reset_config()
    from shared.database import init_db
    await init_db(str(tmp_path / "test.db"))
    from api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    from shared.database import dispose_db
    await dispose_db(str(tmp_path / "test.db"))
    reset_config()


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
