import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch


async def test_post_repos_returns_202(client):
    with patch(
        "api.routers.repos.enqueue_full_index", new_callable=AsyncMock
    ) as mock_eq:
        mock_eq.return_value = "job-uuid-1"
        resp = await client.post(
            "/api/repos", json={"url": "https://github.com/psf/requests"}
        )
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
    with patch(
        "api.routers.repos.enqueue_full_index", new_callable=AsyncMock
    ) as mock_eq:
        mock_eq.return_value = "job-uuid-2"
        await client.post("/api/repos", json={"url": "https://github.com/psf/requests"})
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    repos = resp.json()["repos"]
    assert len(repos) == 1
    assert repos[0]["owner"] == "psf"
    assert repos[0]["platform"] == "github"


async def test_list_repos_orders_by_indexed_at_desc_with_nulls_last(client):
    from shared.config import get_config
    from shared.database import get_session
    from shared.models import Repository

    async with get_session(str(get_config().database_path)) as s:
        s.add_all(
            [
                Repository(
                    id="old",
                    owner="old",
                    name="repo",
                    status="ready",
                    indexed_at=datetime(2024, 1, 1, tzinfo=UTC),
                ),
                Repository(
                    id="missing",
                    owner="missing",
                    name="repo",
                    status="pending",
                    indexed_at=None,
                ),
                Repository(
                    id="recent",
                    owner="recent",
                    name="repo",
                    status="ready",
                    indexed_at=datetime(2024, 2, 1, tzinfo=UTC),
                ),
            ]
        )
        await s.commit()

    resp = await client.get("/api/repos")

    assert resp.status_code == 200
    assert [repo["id"] for repo in resp.json()["repos"]] == [
        "recent",
        "old",
        "missing",
    ]


async def test_refresh_repo_returns_job(client):
    """POST /refresh on a ready repo returns 202 with job_id."""
    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock):
        resp = await client.post(
            "/api/repos", json={"url": "https://github.com/psf/requests"}
        )
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


async def test_post_repos_gitlab_url(client):
    from shared.config import get_config
    from shared.database import get_session
    from shared.models import Repository

    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock):
        resp = await client.post(
            "/api/repos", json={"url": "https://gitlab.com/group/repo"}
        )
    assert resp.status_code == 202
    repo_id = resp.json()["repo_id"]

    async with get_session(str(get_config().database_path)) as s:
        repo = await s.get(Repository, repo_id)
        assert repo is not None
        assert repo.platform == "gitlab"


async def test_post_repos_bitbucket_url(client):
    from shared.config import get_config
    from shared.database import get_session
    from shared.models import Repository

    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock):
        resp = await client.post(
            "/api/repos", json={"url": "https://bitbucket.org/owner/repo"}
        )
    assert resp.status_code == 202
    repo_id = resp.json()["repo_id"]

    async with get_session(str(get_config().database_path)) as s:
        repo = await s.get(Repository, repo_id)
        assert repo is not None
        assert repo.platform == "bitbucket"


async def test_post_repos_gitee_url(client):
    from shared.config import get_config
    from shared.database import get_session
    from shared.models import Repository

    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock):
        resp = await client.post(
            "/api/repos", json={"url": "https://gitee.com/owner/repo"}
        )
    assert resp.status_code == 202
    repo_id = resp.json()["repo_id"]

    async with get_session(str(get_config().database_path)) as s:
        repo = await s.get(Repository, repo_id)
        assert repo is not None
        assert repo.platform == "gitee"


async def test_post_repos_custom_gitlab_domain_url(client):
    from shared.config import get_config
    from shared.database import get_session
    from shared.models import Repository

    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock):
        resp = await client.post(
            "/api/repos",
            json={"url": "gitlab+https://gitlab.internal.example.com/group/repo"},
        )
    assert resp.status_code == 202
    repo_id = resp.json()["repo_id"]

    async with get_session(str(get_config().database_path)) as s:
        repo = await s.get(Repository, repo_id)
        assert repo is not None
        assert repo.platform == "gitlab:gitlab.internal.example.com"


async def test_post_repos_rejects_implicit_custom_gitlab_domain_url(client):
    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock):
        resp = await client.post(
            "/api/repos",
            json={"url": "https://gitlab.internal.example.com/group/repo"},
        )

    assert resp.status_code == 422


async def test_get_repo_includes_platform(client):
    with patch("api.routers.repos.enqueue_full_index", new_callable=AsyncMock):
        resp = await client.post(
            "/api/repos", json={"url": "https://gitlab.com/group/repo"}
        )
    repo_id = resp.json()["repo_id"]

    resp = await client.get(f"/api/repos/{repo_id}")

    assert resp.status_code == 200
    assert resp.json()["platform"] == "gitlab"


async def test_post_repos_unsupported_host(client):
    resp = await client.post(
        "/api/repos", json={"url": "https://codeberg.org/owner/repo"}
    )
    assert resp.status_code == 422
