import pytest
from httpx import AsyncClient, ASGITransport
from shared.models import Repository, WikiPage
from shared.database import get_session
import uuid


@pytest.fixture
async def client_with_wiki(tmp_path):
    import os
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)
    from shared.config import reset_config
    reset_config()
    from shared.database import init_db
    await init_db(str(tmp_path / "test.db"))

    from shared.config import get_config
    cfg = get_config()
    async with get_session(str(cfg.database_path)) as s:
        repo = Repository(id="r1", owner="owner", name="repo", status="ready", platform="github")
        p1 = WikiPage(id=str(uuid.uuid4()), repo_id="r1", slug="overview",
                      title="Overview", content="# Overview\nHello.", page_order=0)
        p2 = WikiPage(id=str(uuid.uuid4()), repo_id="r1", slug="models",
                      title="Models", content="# Models\nClass User.", page_order=1)
        s.add_all([repo, p1, p2])
        await s.commit()

    from api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    from shared.database import dispose_db
    await dispose_db(str(tmp_path / "test.db"))
    reset_config()


async def test_list_wiki_pages(client_with_wiki):
    resp = await client_with_wiki.get("/api/repos/r1/wiki")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["pages"]) == 2
    slugs = [p["slug"] for p in data["pages"]]
    assert "overview" in slugs


async def test_get_wiki_page(client_with_wiki):
    resp = await client_with_wiki.get("/api/repos/r1/wiki/overview")
    assert resp.status_code == 200
    assert resp.json()["content"].startswith("# Overview")


async def test_get_wiki_page_not_found(client_with_wiki):
    resp = await client_with_wiki.get("/api/repos/r1/wiki/nonexistent")
    assert resp.status_code == 404
