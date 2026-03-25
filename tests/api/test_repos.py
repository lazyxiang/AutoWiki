import json
import os
import pytest
from pathlib import Path
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


async def test_refresh_repo_returns_job(client):
    """POST /refresh on a ready repo returns 202 with job_id."""
    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock):
        resp = await client.post("/api/repos", json={"url": "https://github.com/psf/requests"})
    repo_id = resp.json()["repo_id"]

    # Mark repo as ready so refresh is allowed
    db_path = os.environ["DATABASE_PATH"]
    from shared.database import get_session
    from shared.models import Repository
    async with get_session(db_path) as s:
        repo = await s.get(Repository, repo_id)
        repo.status = "ready"
        repo.last_commit = "abc123"
        await s.commit()

    with patch("api.routers.repos.enqueue_refresh_index", new_callable=AsyncMock):
        resp2 = await client.post(f"/api/repos/{repo_id}/refresh")

    assert resp2.status_code == 202
    body = resp2.json()
    assert "job_id" in body
    assert body["status"] == "queued"


async def test_get_graph_returns_nodes(client):
    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock):
        resp = await client.post("/api/repos", json={"url": "https://github.com/psf/requests"})
    repo_id = resp.json()["repo_id"]

    data_dir = os.environ["AUTOWIKI_DATA_DIR"]
    ast_dir = Path(data_dir) / "repos" / repo_id / "ast"
    ast_dir.mkdir(parents=True)
    (ast_dir / "module_tree.json").write_text(
        json.dumps([{"path": "api", "files": ["api/main.py"]},
                    {"path": "worker", "files": ["worker/jobs.py"]}])
    )

    resp2 = await client.get(f"/api/repos/{repo_id}/graph")
    assert resp2.status_code == 200
    body = resp2.json()
    assert "nodes" in body
    assert len(body["nodes"]) == 2
    assert body["nodes"][0]["id"] in ("api", "worker")
