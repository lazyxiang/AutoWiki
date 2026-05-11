"""B5: Deep Research surfaces return the disabled response (issue #43)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client_with_repo(tmp_path):
    import os

    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)
    from shared.config import reset_config

    reset_config()
    from shared.database import get_session, init_db
    from shared.models import Repository

    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    async with get_session(db_path) as s:
        s.add(Repository(id="r1", owner="owner", name="repo", status="ready"))
        await s.commit()

    from api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c

    from shared.database import dispose_db

    await dispose_db(db_path)
    reset_config()


async def test_post_research_returns_503(client_with_repo):
    resp = await client_with_repo.post(
        "/api/repos/r1/research", json={"question": "how does it work?"}
    )
    assert resp.status_code == 503
    assert "temporarily unavailable" in resp.json()["detail"].lower()


async def test_get_research_returns_410(client_with_repo):
    resp = await client_with_repo.get("/api/repos/r1/research/somejobid")
    assert resp.status_code == 410


async def test_get_repo_exposes_features_deep_research_false(client_with_repo):
    resp = await client_with_repo.get("/api/repos/r1")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("features", {}).get("deep_research") is False
