import pytest
import uuid
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock


@pytest.fixture
def client(tmp_path):
    from shared.database import init_db, dispose_db
    from shared.models import Repository
    import asyncio

    db_path = str(tmp_path / "test.db")

    async def _setup():
        from shared.database import get_session
        await init_db(db_path)
        async with get_session(db_path) as s:
            s.add(Repository(id="r1", owner="owner", name="repo", status="ready"))
            await s.commit()

    asyncio.get_event_loop().run_until_complete(_setup())

    with patch("shared.config._config", None), \
         patch("api.routers.chat.get_config") as mock_cfg:
        mock_cfg.return_value.database_path = tmp_path / "test.db"
        mock_cfg.return_value.data_dir = tmp_path
        mock_cfg.return_value.chat.history_window = 10
        from api.main import app
        yield TestClient(app)

    asyncio.get_event_loop().run_until_complete(dispose_db(db_path))


def test_create_chat_session(client):
    resp = client.post("/api/repos/r1/chat")
    assert resp.status_code == 201
    body = resp.json()
    assert "session_id" in body


def test_get_chat_history_empty(client):
    resp = client.post("/api/repos/r1/chat")
    session_id = resp.json()["session_id"]
    resp2 = client.get(f"/api/repos/r1/chat/{session_id}")
    assert resp2.status_code == 200
    assert resp2.json()["messages"] == []


def test_create_chat_session_missing_repo(client):
    resp = client.post("/api/repos/nonexistent/chat")
    assert resp.status_code == 404
